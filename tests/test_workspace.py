from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bscli.core.central_service import CentralCapabilityService
from bscli.core.tasks import TaskNotFound
from bscli.workspace.application import WorkspaceApplication
from bscli.workspace.gateway import (
    GatewayRequestError,
    OpenClawGatewayClient,
)
from bscli.workspace.server import (
    _public_gateway_stream_error,
    create_workspace_http_server,
    validate_workspace_server_config,
)
from bscli.workspace.stores import WorkspaceLinkError, WorkspaceStore


PASSWORD = "AgentBridge!Workspace9"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.stream_events = [
            {
                "type": "progress",
                "runId": "run-web-1",
                "kind": "tool",
                "phase": "start",
                "label": "正在检查 OA 登录状态",
            },
            {
                "type": "chat",
                "runId": "run-web-1",
                "state": "final",
                "text": "OA 登录状态有效。",
            },
        ]

    def call(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout_seconds: float = 30,
    ) -> dict:
        self.calls.append((method, params or {}))
        if method == "system.info":
            return {"version": "2026.7.1"}
        if method == "chat.history":
            return {
                "sessionId": "session-web",
                "messages": [
                    {
                        "role": "user",
                        "content": "读取我的 OA 待办",
                        "timestamp": 1_785_459_303_372,
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "当前有 3 条待办。"},
                            {"type": "tool", "name": "hidden"},
                        ],
                        "timestamp": "1785459333802",
                    },
                    {"role": "tool", "content": "not exposed"},
                ],
            }
        if method == "agentbridge.workspace.bind":
            return {"status": "bound"}
        if method == "chat.send":
            return {"status": "accepted", "runId": "run-web-1"}
        raise AssertionError(f"unexpected Gateway method: {method}")

    def stream(
        self,
        *,
        session_key: str,
        timeout_seconds: float = 30,
    ):
        self.calls.append(
            (
                "stream",
                {
                    "sessionKey": session_key,
                    "timeoutSeconds": timeout_seconds,
                },
            )
        )
        return iter(self.stream_events)

    def send_stream(
        self,
        *,
        session_key: str,
        endpoint_key: str,
        grant: str,
        message: str,
        idempotency_key: str,
        timeout_seconds: float = 150,
    ):
        self.calls.append(
            (
                "send_stream",
                {
                    "sessionKey": session_key,
                    "endpointKey": endpoint_key,
                    "grant": grant,
                    "message": message,
                    "idempotencyKey": idempotency_key,
                    "timeoutSeconds": timeout_seconds,
                },
            )
        )
        return iter(
            [
                {
                    "type": "accepted",
                    "runId": idempotency_key,
                    "status": "started",
                },
                *[
                    {**event, "runId": idempotency_key}
                    for event in self.stream_events
                ],
            ]
        )

    def abort_chat(
        self,
        *,
        session_key: str,
        run_id: str | None = None,
        preserve_side_runs: bool = True,
        timeout_seconds: float = 8,
        raise_on_error: bool = True,
    ) -> dict:
        self.calls.append(
            (
                "chat.abort",
                {
                    "sessionKey": session_key,
                    "runId": run_id,
                    "preserveSideRuns": preserve_side_runs,
                    "timeoutSeconds": timeout_seconds,
                    "raiseOnError": raise_on_error,
                },
            )
        )
        return {"aborted": False}


class WorkspaceStoreTests(unittest.TestCase):
    def test_identity_link_account_session_and_one_use_gateway_grant(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account_a = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            account_b = _create_account(
                service,
                user_subject="user-b",
                username="bob",
                endpoint_key="openclaw-weixin:*:bob",
                client_type="openclaw-weixin",
            )

            self.assertNotEqual(
                account_a["openclaw_session_key"],
                account_b["openclaw_session_key"],
            )
            self.assertNotEqual(account_a["endpoint_id"], account_b["endpoint_id"])
            authenticated = service.workspace.authenticate(
                username="ALICE",
                password=PASSWORD,
            )
            self.assertEqual(authenticated["user_subject"], "user-a")
            self.assertIsNone(
                service.workspace.authenticate(
                    username="alice",
                    password="not-the-password",
                )
            )

            session = service.workspace.create_session(account_a["account_id"])
            verified = service.workspace.verify_session(
                session["session_token"],
                csrf_token=session["csrf_token"],
            )
            self.assertEqual(verified["user_subject"], "user-a")
            self.assertEqual(verified["created_at"], account_a["created_at"])
            self.assertIsNone(
                service.workspace.verify_session(
                    session["session_token"],
                    csrf_token="wrong",
                )
            )

            grant = service.workspace.issue_gateway_grant(account_a["account_id"])
            with self.assertRaises(PermissionError):
                service.workspace.redeem_gateway_grant(
                    grant=grant["grant"],
                    user_subject="user-b",
                    endpoint_key=grant["endpoint_key"],
                    session_key=grant["session_key"],
                )
            redeemed = service.redeem_workspace_gateway_grant(
                user_subject="user-a",
                agent_host="openclaw",
                endpoint_key=grant["endpoint_key"],
                session_key=grant["session_key"],
                grant=grant["grant"],
            )
            self.assertEqual(redeemed["status"], "succeeded")
            resolved = service.resolve_workspace_gateway_session(
                user_subject="user-a",
                agent_host="openclaw",
                session_key=account_a["openclaw_session_key"],
            )
            self.assertEqual(resolved["status"], "succeeded")
            self.assertEqual(
                resolved["binding"]["endpointKey"],
                account_a["endpoint_key"],
            )
            with self.assertRaises(WorkspaceLinkError):
                service.resolve_workspace_gateway_session(
                    user_subject="user-b",
                    agent_host="openclaw",
                    session_key=account_a["openclaw_session_key"],
                )
            with self.assertRaises(WorkspaceLinkError):
                service.workspace.redeem_gateway_grant(
                    grant=grant["grant"],
                    user_subject="user-a",
                    endpoint_key=grant["endpoint_key"],
                    session_key=grant["session_key"],
                )

    def test_session_idle_timeout_and_logout_do_not_unlink_identity(self) -> None:
        clock = MutableClock()
        with TemporaryDirectory() as tmp:
            store = WorkspaceStore(
                Path(tmp) / "agentbridge.db",
                clock=clock,
                session_ttl_seconds=600,
                session_idle_seconds=120,
            )
            link = store.start_link()
            store.confirm_link(
                link_code=link["link_code"],
                user_subject="user-a",
                approver_endpoint_id="endpoint-a",
            )
            account = store.create_account(
                enrollment_token=link["enrollment_token"],
                username="alice",
                password=PASSWORD,
            )
            session = store.create_session(account["account_id"])

            store.revoke_session(session["session_token"])
            self.assertIsNone(store.verify_session(session["session_token"]))
            self.assertEqual(
                store.authenticate(username="alice", password=PASSWORD)[
                    "user_subject"
                ],
                "user-a",
            )

            fresh = store.create_session(account["account_id"])
            clock.value += timedelta(seconds=121)
            self.assertIsNone(store.verify_session(fresh["session_token"]))

    def test_account_completion_recovers_after_endpoint_registration_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            link = service.workspace.start_link()
            service.workspace.confirm_link(
                link_code=link["link_code"],
                user_subject="user-a",
                approver_endpoint_id="endpoint-a",
            )
            created = service.workspace.create_account(
                enrollment_token=link["enrollment_token"],
                username="alice",
                password=PASSWORD,
            )

            completed = WorkspaceApplication(
                service=service
            ).complete_enrollment(
                enrollment_token=link["enrollment_token"],
                username="alice",
                password=PASSWORD,
            )

            self.assertEqual(
                completed["account"]["accountId"],
                created["account_id"],
            )
            self.assertIsNotNone(
                service.workspace.get_account(created["account_id"])[
                    "endpoint_id"
                ]
            )

    def test_workspace_tasks_events_and_endpoints_are_user_isolated(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account_a = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            account_b = _create_account(
                service,
                user_subject="user-b",
                username="bob",
                endpoint_key="telegram:*:bob",
            )
            app = WorkspaceApplication(service=service)
            task_a = service.ensure_host_task(
                user_subject="user-a",
                token_id="token-a",
                agent_host="openclaw",
                host_task_key="session-a|run-a",
                endpoint_key="telegram:*:alice",
                client_type="telegram",
                external_subject="alice",
                conversation_ref="agent:main:telegram:direct:alice",
                title="Alice OA task",
            )
            service.ensure_host_task(
                user_subject="user-b",
                token_id="token-b",
                agent_host="openclaw",
                host_task_key="session-b|run-b",
                endpoint_key="telegram:*:bob",
                client_type="telegram",
                external_subject="bob",
                conversation_ref="agent:main:telegram:direct:bob",
                title="Bob OA task",
            )

            self.assertEqual(
                [item["title"] for item in app.list_tasks(account_a)],
                ["Alice OA task"],
            )
            self.assertEqual(
                [item["title"] for item in app.list_tasks(account_b)],
                ["Bob OA task"],
            )
            self.assertTrue(
                all(
                    "user_subject" not in item
                    for item in app.list_events(account_a)
                )
            )
            self.assertEqual(
                app.latest_event_id(account_a),
                app.list_events(account_a)[-1]["event_id"],
            )
            self.assertTrue(
                all(
                    "user_subject" not in item
                    and "token_id" not in item
                    and "conversation_ref" not in item
                    for item in app.list_endpoints(account_a)
                )
            )
            with self.assertRaises(TaskNotFound):
                app.task_detail(
                    account_b,
                    task_a["task"]["taskId"],
                )

    def test_password_hash_and_tokens_are_not_stored_in_plaintext(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            session = service.workspace.create_session(account["account_id"])
            with closing(sqlite3.connect(service.db_path)) as connection:
                password_hash = connection.execute(
                    "SELECT password_hash FROM workspace_accounts"
                ).fetchone()[0]
                token_hash, csrf_hash = connection.execute(
                    "SELECT token_hash, csrf_hash FROM workspace_sessions "
                    "WHERE session_id = ?",
                    (session["session_id"],),
                ).fetchone()
            self.assertNotIn(PASSWORD, password_hash)
            self.assertNotEqual(token_hash, session["session_token"])
            self.assertNotEqual(csrf_hash, session["csrf_token"])


class WorkspaceApplicationTests(unittest.TestCase):
    def test_authenticated_request_touches_workspace_endpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            session = service.workspace.create_session(account["account_id"])
            stale = "2026-01-01T00:00:00+00:00"
            with closing(sqlite3.connect(service.db_path)) as connection:
                connection.execute(
                    """
                    UPDATE client_endpoints
                    SET updated_at = ?, last_seen_at = ?
                    WHERE endpoint_id = ?
                    """,
                    (stale, stale, account["endpoint_id"]),
                )

            app = WorkspaceApplication(service=service)
            verified = app.session(session["session_token"])
            endpoint = service.tasks.endpoint_for_key(
                user_subject="user-a",
                agent_host="openclaw",
                endpoint_key=account["endpoint_key"],
            )

            self.assertIsNotNone(verified)
            self.assertNotEqual(endpoint["last_seen_at"], stale)
            self.assertEqual(endpoint["user_subject"], "user-a")

    def test_continue_task_binds_owned_task_to_workspace_endpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            task = service.ensure_host_task(
                user_subject="user-a",
                token_id="workspace-token",
                agent_host="openclaw",
                host_task_key="workspace-task|continue-1",
                endpoint_key=account["endpoint_key"],
                client_type="web",
                external_subject=account["account_id"],
                conversation_ref=account["openclaw_session_key"],
                title="Review pending workflow",
            )
            app = WorkspaceApplication(service=service)

            result = app.continue_task(account, task["task"]["taskId"])
            continuation = service.tasks.get_continuation(
                user_subject="user-a",
                agent_host="openclaw",
                endpoint_id=account["endpoint_id"],
            )

            self.assertEqual(result["status"], "selected")
            self.assertEqual(result["task"]["task_id"], task["task"]["taskId"])
            self.assertEqual(continuation["state"], "selected")
            self.assertEqual(continuation["execution_mode"], "resume")
            self.assertEqual(
                continuation["selected_task_id"],
                task["task"]["taskId"],
            )

    def test_task_detail_uses_the_workspace_endpoint_authorization_url(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            task = service.ensure_host_task(
                user_subject="user-a",
                token_id="workspace-token",
                agent_host="openclaw",
                host_task_key="workspace-task|run-1",
                endpoint_key=account["endpoint_key"],
                client_type="web",
                external_subject=account["account_id"],
                conversation_ref=account["openclaw_session_key"],
                title="Submit leave request",
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
            interaction = service._execution_authorization_interaction(
                authorization
            )
            service.observe_host_task(
                user_subject="user-a",
                task_id=task["task"]["taskId"],
                interaction_ids=[interaction["interactionId"]],
            )
            app = WorkspaceApplication(service=service)

            detail = app.task_detail(account, task["task"]["taskId"])

            presentation = detail["interaction"]["presentation"]
            self.assertTrue(presentation["individualized"])
            self.assertEqual(presentation["endpointId"], account["endpoint_id"])
            self.assertIn("/present/", presentation["url"])

    def test_chat_binds_identity_before_send_and_hides_tool_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            service.tasks.ensure_endpoint(
                user_subject="user-a",
                token_id="token-alice",
                agent_host="openclaw",
                endpoint_key="telegram:*:alice",
                client_type="telegram",
                external_subject="alice",
                conversation_ref="agent:main:telegram:direct:alice",
                capabilities=["direct_status", "timeline_message"],
                route={"channel": "telegram", "to": "alice"},
            )
            gateway = FakeGateway()
            app = WorkspaceApplication(service=service, gateway=gateway)

            history = app.chat_history(account)
            streamed = list(app.chat_stream(account))
            send_streamed = list(
                app.send_chat_stream(
                    account,
                    message="读取我的 OA 待办",
                    idempotency_key="web-message-stream-1",
                )
            )
            result = app.send_chat(
                account,
                message="读取我的 OA 待办",
                idempotency_key="web-message-1",
            )

            self.assertEqual(
                [item["role"] for item in history["messages"]],
                ["user", "assistant"],
            )
            self.assertEqual(
                history["messages"][0]["timestamp"],
                datetime.fromtimestamp(
                    1_785_459_303.372,
                    timezone.utc,
                ).isoformat(timespec="milliseconds"),
            )
            self.assertEqual(
                history["messages"][1]["timestamp"],
                datetime.fromtimestamp(
                    1_785_459_333.802,
                    timezone.utc,
                ).isoformat(timespec="milliseconds"),
            )
            self.assertEqual(result.run_id, "web-message-1")
            self.assertEqual(streamed[-1]["state"], "final")
            self.assertEqual(send_streamed[0]["type"], "accepted")
            methods = [method for method, _params in gateway.calls]
            self.assertEqual(
                methods,
                [
                    "chat.history",
                    "stream",
                    "send_stream",
                    "send_stream",
                ],
            )
            streamed_send = gateway.calls[2][1]
            compatibility_send = gateway.calls[3][1]
            self.assertEqual(
                streamed_send["sessionKey"],
                account["openclaw_session_key"],
            )
            self.assertEqual(
                streamed_send["idempotencyKey"],
                "web-message-stream-1",
            )
            self.assertEqual(streamed_send["timeoutSeconds"], 300)
            self.assertEqual(
                compatibility_send["sessionKey"],
                account["openclaw_session_key"],
            )
            self.assertEqual(
                compatibility_send["idempotencyKey"],
                "web-message-1",
            )
            timeline = app.list_timeline(account)
            messages = [
                entry
                for entry in timeline
                if entry["entry_type"] == "chat_message"
            ]
            self.assertEqual(
                [entry["role"] for entry in messages],
                ["user", "assistant", "user", "assistant"],
            )
            self.assertTrue(
                all(entry["source"]["is_origin"] for entry in messages)
            )
            self.assertEqual(
                [entry["sequence"] for entry in messages],
                sorted(entry["sequence"] for entry in messages),
            )
            telegram_endpoint = service.tasks.endpoint_for_key(
                user_subject="user-a",
                agent_host="openclaw",
                endpoint_key="telegram:*:alice",
            )
            timeline_deliveries = [
                delivery
                for delivery in service.tasks.list_outbox(
                    user_subject="user-a",
                    endpoint_id=telegram_endpoint["endpoint_id"],
                )
                if delivery["payload_type"] == "timeline_message"
            ]
            self.assertEqual(len(timeline_deliveries), 4)

    def test_timeline_is_isolated_by_workspace_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account_a = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            account_b = _create_account(
                service,
                user_subject="user-b",
                username="bob",
                endpoint_key="wechat:direct:bob",
                client_type="wechat",
            )
            service.tasks.append_timeline_message(
                user_subject="user-a",
                source_endpoint_id=account_a["endpoint_id"],
                message_key="a-1",
                role="user",
                text="Message for Alice",
            )
            service.tasks.append_timeline_message(
                user_subject="user-b",
                source_endpoint_id=account_b["endpoint_id"],
                message_key="b-1",
                role="user",
                text="Message for Bob",
            )
            app = WorkspaceApplication(service=service)

            self.assertEqual(
                [entry["text"] for entry in app.list_timeline(account_a)],
                ["Message for Alice"],
            )
            self.assertEqual(
                [entry["text"] for entry in app.list_timeline(account_b)],
                ["Message for Bob"],
            )

    def test_gateway_failure_is_projected_to_the_cross_end_timeline(self) -> None:
        class FailingGateway(FakeGateway):
            def send_stream(self, **kwargs):
                super().send_stream(**kwargs)
                raise GatewayRequestError(
                    "GATEWAY_RUN_TIMEOUT_ABORTED",
                    "run stopped",
                    {"hadToolActivity": False},
                )

        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            app = WorkspaceApplication(
                service=service,
                gateway=FailingGateway(),
            )

            with self.assertRaises(GatewayRequestError) as caught:
                list(
                    app.send_chat_stream(
                        account,
                        message="List five sent workflows",
                        idempotency_key="failed-run-1",
                    )
                )

            self.assertEqual(
                caught.exception.code,
                "GATEWAY_RUN_TIMEOUT_ABORTED",
            )
            timeline = app.list_timeline(account)
            self.assertEqual(
                [entry["role"] for entry in timeline],
                ["user", "assistant"],
            )
            self.assertIn("\u5b89\u5168\u4e2d\u6b62", timeline[-1]["text"])

    def test_terminal_abort_keeps_tool_aware_text_in_the_timeline(self) -> None:
        class AbortedGateway(FakeGateway):
            def send_stream(self, **kwargs):
                self.calls.append(("send_stream", kwargs))
                return iter(
                    [
                        {
                            "type": "accepted",
                            "runId": "aborted-run-1",
                            "status": "started",
                        },
                        {
                            "type": "chat",
                            "runId": "aborted-run-1",
                            "state": "aborted",
                            "hadToolActivity": True,
                            "safeToRetry": False,
                            "text": "Run stopped after a business tool call.",
                        },
                    ]
                )

        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            app = WorkspaceApplication(
                service=service,
                gateway=AbortedGateway(),
            )

            list(
                app.send_chat_stream(
                    account,
                    message="Read OA pending workflows",
                    idempotency_key="aborted-run-1",
                )
            )

            self.assertEqual(
                app.list_timeline(account)[-1]["text"],
                "Run stopped after a business tool call.",
            )

    def test_second_workspace_run_is_rejected_instead_of_queued(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingGateway(FakeGateway):
            def send_stream(self, **kwargs):
                self.calls.append(("send_stream", kwargs))

                def events():
                    yield {
                        "type": "accepted",
                        "runId": kwargs["idempotency_key"],
                        "status": "started",
                    }
                    entered.set()
                    release.wait(timeout=5)
                    yield {
                        "type": "chat",
                        "runId": kwargs["idempotency_key"],
                        "state": "final",
                        "text": "done",
                    }

                return events()

        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            account = _create_account(
                service,
                user_subject="user-a",
                username="alice",
                endpoint_key="telegram:*:alice",
            )
            gateway = BlockingGateway()
            app = WorkspaceApplication(service=service, gateway=gateway)
            first_errors: list[Exception] = []

            def consume_first() -> None:
                try:
                    list(
                        app.send_chat_stream(
                            account,
                            message="first",
                            idempotency_key="concurrent-run-1",
                        )
                    )
                except Exception as exc:  # pragma: no cover - assertion aid
                    first_errors.append(exc)

            thread = threading.Thread(target=consume_first)
            thread.start()
            self.assertTrue(entered.wait(timeout=3))
            try:
                with self.assertRaises(GatewayRequestError) as caught:
                    list(
                        app.send_chat_stream(
                            account,
                            message="second",
                            idempotency_key="concurrent-run-2",
                        )
                    )
                self.assertEqual(
                    caught.exception.code,
                    "WORKSPACE_RUN_IN_PROGRESS",
                )
            finally:
                release.set()
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(first_errors, [])
            self.assertEqual(
                sum(method == "send_stream" for method, _ in gateway.calls),
                1,
            )
            timeline = app.list_timeline(account)
            self.assertTrue(
                any(
                    entry["role"] == "assistant"
                    and "\u6ca1\u6709\u6392\u961f" in entry["text"]
                    for entry in timeline
                )
            )


class WorkspaceGatewayClientTests(unittest.TestCase):
    def test_gateway_client_advertises_tool_event_streaming(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "bscli"
            / "workspace"
            / "gateway_client.mjs"
        ).read_text(encoding="utf-8")

        self.assertIn('caps: ["tool-events"]', source)
        self.assertIn('method: "chat.abort"', source)
        self.assertIn("preflightAbortRequestId", source)
        self.assertIn("requestWorkspaceBind", source)
        self.assertIn('method: "sessions.list"', source)
        self.assertIn('requestStreamAbort(recover ? "startup_recovery"', source)
        self.assertIn("startRecoveryAttempt", source)
        self.assertIn("GATEWAY_RUN_TIMEOUT_ABORTED", source)

    def test_gateway_token_is_passed_only_through_the_child_environment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
                script_path=root / "gateway_client.mjs",
            )
            with patch(
                "bscli.workspace.gateway.subprocess.run"
            ) as run:
                run.return_value.stdout = json.dumps(
                    {"ok": True, "payload": {"version": "2026.7.1"}}
                )
                run.return_value.returncode = 0

                result = client.call("system.info", {})

            self.assertEqual(result["version"], "2026.7.1")
            command = run.call_args.args[0]
            self.assertNotIn("gateway-secret-token-value", command)
            self.assertNotIn(
                "gateway-secret-token-value",
                run.call_args.kwargs["input"],
            )
            self.assertEqual(
                run.call_args.kwargs["env"]["AB_GATEWAY_TOKEN"],
                "gateway-secret-token-value",
            )

    def test_gateway_error_code_is_preserved_without_stderr_leakage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
            )
            with patch(
                "bscli.workspace.gateway.subprocess.run"
            ) as run:
                run.return_value.stdout = json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "PAIRING_REQUIRED",
                            "message": "Device approval is required.",
                        },
                    }
                )
                run.return_value.returncode = 0
                with self.assertRaises(GatewayRequestError) as caught:
                    client.call("system.info", {})

            self.assertEqual(caught.exception.code, "PAIRING_REQUIRED")
            self.assertNotIn(
                "gateway-secret-token-value",
                str(caught.exception),
            )

    def test_gateway_send_stream_keeps_secrets_out_of_child_arguments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
            )
            input_pipe = _CaptureInput()
            with patch(
                "bscli.workspace.gateway.subprocess.Popen"
            ) as popen:
                process = popen.return_value
                process.stdin = input_pipe
                process.stdout = iter(
                    [
                        '{"type":"accepted","runId":"run-1",'
                        '"status":"started"}\n',
                        '{"type":"progress","runId":"run-1",'
                        '"kind":"tool","phase":"start",'
                        '"label":"正在检查 OA 登录状态"}\n',
                        '{"type":"chat","runId":"run-1",'
                        '"state":"final","text":"已完成"}\n',
                        '{"type":"eof"}\n',
                    ]
                )
                process.poll.return_value = 0

                events = list(
                    client.send_stream(
                        session_key=(
                            "agent:main:agentbridge-workspace:direct:account-a"
                        ),
                        endpoint_key="workspace:account-a",
                        grant="g" * 48,
                        message="检查 OA 登录状态",
                        idempotency_key="run-1",
                        timeout_seconds=150,
                    )
                )

            self.assertEqual(
                [event["type"] for event in events],
                ["accepted", "progress", "chat"],
            )
            request = json.loads(input_pipe.value)
            self.assertEqual(request["mode"], "send-stream")
            self.assertEqual(request["idempotencyKey"], "run-1")
            self.assertTrue(request["preflightAbort"])
            self.assertEqual(request["acceptTimeoutMs"], 35_000)
            self.assertEqual(request["startupProgressTimeoutMs"], 15_000)
            self.assertEqual(request["sessionIdleTimeoutMs"], 15_000)
            self.assertEqual(request["sessionIdlePollMs"], 250)
            self.assertNotIn("gateway-secret-token-value", input_pipe.value)
            command = popen.call_args.args[0]
            self.assertNotIn("gateway-secret-token-value", command)
            self.assertEqual(
                popen.call_args.kwargs["env"]["AB_GATEWAY_TOKEN"],
                "gateway-secret-token-value",
            )

    def test_closing_an_accepted_stream_aborts_the_openclaw_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
            )
            input_pipe = _CaptureInput()
            with (
                patch("bscli.workspace.gateway.subprocess.Popen") as popen,
                patch.object(client, "abort_chat") as abort_chat,
            ):
                process = popen.return_value
                process.stdin = input_pipe
                process.stdout = iter(
                    [
                        '{"type":"accepted","runId":"run-close-1",'
                        '"status":"started"}\n',
                    ]
                )
                process.poll.return_value = 0
                stream = client.send_stream(
                    session_key=(
                        "agent:main:agentbridge-workspace:direct:account-a"
                    ),
                    endpoint_key="workspace:account-a",
                    grant="g" * 48,
                    message="List five sent workflows",
                    idempotency_key="run-close-1",
                    timeout_seconds=120,
                )

                self.assertEqual(next(stream)["type"], "accepted")
                stream.close()

            abort_chat.assert_called_once_with(
                session_key=(
                    "agent:main:agentbridge-workspace:direct:account-a"
                ),
                run_id="run-close-1",
                timeout_seconds=5,
                raise_on_error=False,
            )

    def test_gateway_send_stream_retries_one_pre_accept_handshake_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
            )
            payloads = []

            def stream_payload(payload):
                payloads.append(payload)
                if len(payloads) == 1:
                    def failed_stream():
                        raise GatewayRequestError(
                            "GATEWAY_CONNECTION_CLOSED",
                            "connection closed",
                            {"stage": "connect", "accepted": False},
                        )
                        yield  # pragma: no cover

                    return failed_stream()
                return iter(
                    [
                        {
                            "type": "accepted",
                            "runId": "run-retry-1",
                            "status": "started",
                        },
                        {
                            "type": "chat",
                            "runId": "run-retry-1",
                            "state": "final",
                            "text": "done",
                        },
                    ]
                )

            with patch.object(
                client,
                "_stream_payload",
                side_effect=stream_payload,
            ):
                events = list(
                    client.send_stream(
                        session_key=(
                            "agent:main:agentbridge-workspace:direct:account-a"
                        ),
                        endpoint_key="workspace:account-a",
                        grant="g" * 48,
                        message="List five sent workflows",
                        idempotency_key="run-retry-1",
                        timeout_seconds=120,
                    )
                )

            self.assertEqual(len(payloads), 2)
            self.assertEqual(
                [event["type"] for event in events],
                ["progress", "accepted", "chat"],
            )
            self.assertEqual(events[0]["phase"], "retry")

    def test_gateway_send_stream_does_not_retry_after_send_started(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / "gateway.token"
            token_file.write_text(
                "gateway-secret-token-value",
                encoding="utf-8",
            )
            client = OpenClawGatewayClient(
                url="ws://127.0.0.1:18789",
                token_file=token_file,
                state_dir=root / "state",
            )
            calls = 0

            def stream_payload(_payload):
                nonlocal calls
                calls += 1

                def failed_stream():
                    raise GatewayRequestError(
                        "GATEWAY_CONNECTION_CLOSED",
                        "connection closed",
                        {"stage": "send_accept", "accepted": False},
                    )
                    yield  # pragma: no cover

                return failed_stream()

            with (
                patch.object(
                    client,
                    "_stream_payload",
                    side_effect=stream_payload,
                ),
                self.assertRaises(GatewayRequestError),
            ):
                list(
                    client.send_stream(
                        session_key=(
                            "agent:main:agentbridge-workspace:direct:account-a"
                        ),
                        endpoint_key="workspace:account-a",
                        grant="g" * 48,
                        message="List five sent workflows",
                        idempotency_key="run-no-retry-1",
                        timeout_seconds=120,
                    )
                )

            self.assertEqual(calls, 1)


class WorkspaceHttpServerTests(unittest.TestCase):
    def test_stream_error_exposes_only_safe_retry_diagnostics(self) -> None:
        payload = _public_gateway_stream_error(
            GatewayRequestError(
                "GATEWAY_RUN_TIMEOUT_ABORTED",
                "stopped",
                {
                    "runId": "private-run-id",
                    "aborted": True,
                    "hadToolActivity": False,
                    "recoveryUsed": True,
                    "recoveryAttempt": 1,
                    "acceptedElapsedMs": 1234,
                },
            )
        )

        self.assertTrue(payload["safeToRetry"])
        self.assertNotIn("runId", payload["details"])
        self.assertEqual(payload["details"]["recoveryAttempt"], 1)

    def test_enrollment_login_csrf_and_read_only_workspace_routes(self) -> None:
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            gateway = FakeGateway()
            application = WorkspaceApplication(service=service, gateway=gateway)
            port = _free_port()
            origin = f"http://127.0.0.1:{port}"
            server = create_workspace_http_server(
                config=validate_workspace_server_config(
                    host="127.0.0.1",
                    port=port,
                    public_base_url=origin,
                    tls_cert=None,
                    tls_key=None,
                ),
                application=application,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, link = _request(
                    port,
                    "POST",
                    "/api/enrollment/start",
                    body={},
                    origin=origin,
                )
                self.assertEqual(status, 201)
                cookies = _cookies(headers)
                service.confirm_workspace_link(
                    user_subject="user-a",
                    token_id="token-a",
                    agent_host="openclaw",
                    endpoint_key="telegram:*:alice",
                    client_type="telegram",
                    external_subject="alice",
                    conversation_ref="agent:main:telegram:direct:alice",
                    link_code=link["linkCode"],
                )
                status, headers, completed = _request(
                    port,
                    "POST",
                    "/api/enrollment/complete",
                    body={"username": "alice", "password": PASSWORD},
                    origin=origin,
                    cookies=cookies,
                )
                self.assertEqual(status, 201)
                self.assertTrue(completed["authenticated"])
                cookies.update(_cookies(headers))

                status, response_headers, session = _request(
                    port,
                    "GET",
                    "/api/session",
                    cookies=cookies,
                )
                self.assertEqual(status, 200)
                self.assertTrue(session["authenticated"])
                self.assertEqual(
                    response_headers.get("X-Frame-Options"),
                    "DENY",
                )
                self.assertNotIn(
                    "userSubject",
                    json.dumps(session, ensure_ascii=False),
                )

                status, _, error = _request(
                    port,
                    "POST",
                    "/api/chat/send",
                    body={"message": "读取待办"},
                    origin=origin,
                    cookies=cookies,
                )
                self.assertEqual(status, 401)
                self.assertEqual(
                    error["error"]["code"],
                    "AUTHENTICATION_REQUIRED",
                )

                status, _, accepted = _request(
                    port,
                    "POST",
                    "/api/chat/send",
                    body={"message": "读取待办"},
                    origin=origin,
                    cookies=cookies,
                    csrf=cookies["agentbridge_workspace_csrf"],
                )
                self.assertEqual(status, 202)
                self.assertTrue(accepted["runId"])

                status, response_headers, stream = _raw_request(
                    port,
                    "POST",
                    "/api/chat/send-stream",
                    body={
                        "message": "读取待办",
                        "idempotencyKey": "web-stream-1",
                    },
                    origin=origin,
                    cookies=cookies,
                    csrf=cookies["agentbridge_workspace_csrf"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    response_headers.get_content_type(),
                    "text/event-stream",
                )
                self.assertIn("event: progress", stream)
                self.assertIn("event: chat", stream)
                self.assertNotIn("user_subject", stream)

                status, _, timeline = _request(
                    port,
                    "GET",
                    "/api/timeline?limit=20",
                    cookies=cookies,
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    [
                        item["role"]
                        for item in timeline["items"]
                        if item["entry_type"] == "chat_message"
                    ],
                    ["user", "assistant", "user", "assistant"],
                )
                self.assertEqual(
                    timeline["cursor"],
                    max(item["sequence"] for item in timeline["items"]),
                )
                self.assertNotIn(
                    "user_subject",
                    json.dumps(timeline, ensure_ascii=False),
                )

                status, _, endpoints = _request(
                    port,
                    "GET",
                    "/api/endpoints",
                    cookies=cookies,
                )
                self.assertEqual(status, 200)
                self.assertTrue(endpoints["items"])
                self.assertTrue(
                    all(
                        "user_subject" not in item
                        and "token_id" not in item
                        and "route" not in item
                        for item in endpoints["items"]
                    )
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class WorkspaceStaticAssetTests(unittest.TestCase):
    def test_assets_are_csp_clean_and_mobile_detail_has_back_control(self) -> None:
        root = Path(__file__).resolve().parents[1] / "bscli" / "workspace" / "static"
        page = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "workspace.js").read_text(encoding="utf-8")
        stylesheet = (root / "workspace.css").read_text(encoding="utf-8")

        self.assertNotIn('style="', page)
        self.assertNotIn("font-size: clamp(", stylesheet)
        self.assertNotIn("linear-gradient(", stylesheet)
        self.assertIn("mobile-detail-back", script)
        self.assertIn("mobile-detail-back", stylesheet)
        self.assertIn('class="task-detail mobile-empty"', page)
        self.assertIn("const loginForm = event.currentTarget;", script)
        self.assertIn("const enrollmentForm = event.currentTarget;", script)
        self.assertIn("页面已刷新，请重新生成配对码。", script)
        self.assertNotIn("setBusy(event.currentTarget", script)
        self.assertIn('fetch("/api/chat/send-stream"', script)
        self.assertIn("response.body.getReader()", script)
        self.assertIn("parseSseBlock", script)
        self.assertIn("handleChatProgress", script)
        self.assertIn("handleChatDelta", script)
        self.assertIn("const previousRunId = runId", script)
        self.assertIn("adoptLiveMessage(previousRunId, runId", script)
        self.assertIn("streamFailure.safeToRetry === true", script)
        self.assertIn("hydrateTaskCards", script)
        self.assertIn("upsertTaskCard", script)
        self.assertIn('api("/api/timeline?limit=240")', script)
        self.assertIn("openTimelineStream", script)
        self.assertIn("renderChatTimeline", script)
        self.assertEqual(script.count("dismissTerminalLiveMessages();"), 2)
        self.assertIn('classList.contains("failed")', script)
        self.assertIn("parseTimestampMilliseconds", script)
        self.assertIn(r"/^\d{10,13}$/", script)
        self.assertIn("state.taskCards.get(task.task_id)", script)
        self.assertIn("displayTaskTitle", script)
        self.assertIn("OA 出差申请提交", script)
        self.assertIn('source.addEventListener("cursor"', script)
        self.assertNotIn('"任务状态已更新",', script)
        self.assertIn("childElementCount > 2", script)
        self.assertIn("application-card", stylesheet)
        self.assertIn('continueButton.textContent = "继续任务"', script)
        self.assertIn("/continue`,", script)
        self.assertIn("detail-heading-actions", stylesheet)


def _service(tmp: str) -> CentralCapabilityService:
    return CentralCapabilityService(
        home=tmp,
        base_url="http://127.0.0.1:8000/seeyon",
    )


def _create_account(
    service: CentralCapabilityService,
    *,
    user_subject: str,
    username: str,
    endpoint_key: str,
    client_type: str = "telegram",
) -> dict:
    link = service.workspace.start_link()
    service.confirm_workspace_link(
        user_subject=user_subject,
        token_id=f"token-{username}",
        agent_host="openclaw",
        endpoint_key=endpoint_key,
        client_type=client_type,
        external_subject=username,
        conversation_ref=f"agent:main:{client_type}:direct:{username}",
        link_code=link["link_code"],
    )
    account = service.workspace.create_account(
        enrollment_token=link["enrollment_token"],
        username=username,
        password=PASSWORD,
    )
    return service.register_workspace_endpoint(
        account_id=account["account_id"],
    )["account"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    origin: str | None = None,
    cookies: dict[str, str] | None = None,
    csrf: str | None = None,
) -> tuple[int, http.client.HTTPMessage, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": f"127.0.0.1:{port}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if cookies:
        headers["Cookie"] = "; ".join(
            f"{name}={value}" for name, value in cookies.items()
        )
    if csrf:
        headers["X-AgentBridge-CSRF"] = csrf
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    parsed = json.loads(raw.decode("utf-8")) if raw else {}
    result = (response.status, response.headers, parsed)
    connection.close()
    return result


def _raw_request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    origin: str | None = None,
    cookies: dict[str, str] | None = None,
    csrf: str | None = None,
) -> tuple[int, http.client.HTTPMessage, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": f"127.0.0.1:{port}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if cookies:
        headers["Cookie"] = "; ".join(
            f"{name}={value}" for name, value in cookies.items()
        )
    if csrf:
        headers["X-AgentBridge-CSRF"] = csrf
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    result = (response.status, response.headers, raw)
    connection.close()
    return result


def _cookies(headers: http.client.HTTPMessage) -> dict[str, str]:
    result = {}
    for value in headers.get_all("Set-Cookie") or []:
        pair = value.split(";", 1)[0]
        name, cookie_value = pair.split("=", 1)
        result[name] = cookie_value
    return result


class _CaptureInput:
    def __init__(self) -> None:
        self.value = ""

    def write(self, value: str) -> int:
        self.value += value
        return len(value)

    def close(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
