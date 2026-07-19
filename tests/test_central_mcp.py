import json
import threading
import warnings
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient

from bscli.core.central_service import CentralCapabilityService
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.auth.server import AuthServerConfig
from bscli.mcp.central import (
    CentralSessionKeepalive,
    create_central_mcp_server,
    serve_central_mcp,
    validate_central_mcp_server_config,
)
from bscli.mcp.presentation import (
    MCP_APP_MIME_TYPE,
    MCP_APP_RESOURCE_URI,
    MCP_PROFILE_RESOURCE_URI,
    PRIVATE_INTERACTION_META_KEY,
)


class CentralMcpTests(unittest.TestCase):
    def test_controlled_keepalive_worker_runs_and_stops(self):
        service = MagicMock()
        called = threading.Event()
        service.run_session_keepalive_cycle.side_effect = lambda **_kwargs: (
            called.set()
            or {
                "activeSessions": 1,
                "eligibleSessions": 1,
                "keptAlive": 1,
                "expired": 0,
                "deferred": 0,
                "outsideLease": 0,
            }
        )
        keepalive = CentralSessionKeepalive(
            service,
            interval_seconds=0.01,
            activity_lease_seconds=1,
            initial_delay_seconds=0,
        )

        keepalive.start()
        self.assertTrue(called.wait(timeout=1))
        keepalive.stop()

        service.run_session_keepalive_cycle.assert_called_with(
            activity_lease_seconds=1,
        )

    def test_non_loopback_server_requires_https_and_tls(self):
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            validate_central_mcp_server_config(
                host="0.0.0.0",
                port=8790,
                public_base_url="http://mcp.example.test",
                tls_cert=None,
                tls_key=None,
            )

    def test_explicit_private_ip_http_is_allowed_for_restricted_poc(self):
        config = validate_central_mcp_server_config(
            host="10.20.30.40",
            port=8790,
            public_base_url="http://10.20.30.40:8790",
            tls_cert=None,
            tls_key=None,
            allow_insecure_private_http=True,
        )

        self.assertEqual(config.mcp_url, "http://10.20.30.40:8790/mcp")
        self.assertTrue(config.insecure_private_http)

    def test_private_ip_http_mcp_accepts_its_configured_host(self):
        with TemporaryDirectory() as tmp:
            service = MagicMock()
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db")
            issued = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                ttl_seconds=3600,
            )
            config = validate_central_mcp_server_config(
                host="10.20.30.40",
                port=8790,
                public_base_url="http://10.20.30.40:8790",
                tls_cert=None,
                tls_key=None,
                allow_insecure_private_http=True,
            )
            server = create_central_mcp_server(
                service=service,
                identity_store=store,
                config=config,
                auth_card_base_url="http://10.20.30.40:8780",
            )
            with TestClient(
                server.streamable_http_app(),
                base_url="http://10.20.30.40:8790",
            ) as client:
                response = self._request(
                    client,
                    "tools/list",
                    request_id=1,
                    token=issued["token"],
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("tools", response.json()["result"])

    def test_unauthenticated_request_is_rejected(self):
        with self._server() as (_service, _store, _token, client):
            response = self._request(client, "tools/list", request_id=1, authenticated=False)

        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("www-authenticate", ""))

    def test_tool_catalog_separates_reads_and_governed_writes_without_user_subject(self):
        with self._server() as (_service, _store, token, client):
            response = self._request(client, "tools/list", request_id=1, token=token)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        tools = payload["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertIn("oa_workflow_pending_list", names)
        self.assertIn("oa_workflow_detail_get", names)
        self.assertIn("oa_session_login", names)
        self.assertIn("agentbridge_operation_list", names)
        self.assertIn("agentbridge_interaction_get", names)
        self.assertIn("agentbridge_interaction_resume", names)
        self.assertIn("agentbridge_server_profile", names)
        self.assertIn("oa_business_trip_prepare", names)
        self.assertIn("oa_business_trip_save_draft", names)
        self.assertIn("oa_business_trip_submit_prepare", names)
        self.assertIn("oa_business_trip_submit", names)
        self.assertIn("oa_leave_prepare", names)
        self.assertIn("oa_leave_save_draft", names)
        self.assertIn("oa_missed_punch_prepare", names)
        self.assertIn("oa_missed_punch_save_draft", names)
        self.assertIn("oa_missed_punch_approval_prepare", names)
        self.assertIn("oa_missed_punch_approve", names)
        self.assertIn("oa_meeting_create_prepare", names)
        self.assertIn("oa_meeting_create", names)
        pending = next(tool for tool in tools if tool["name"] == "oa_workflow_pending_list")
        prepare = next(tool for tool in tools if tool["name"] == "oa_business_trip_prepare")
        save = next(tool for tool in tools if tool["name"] == "oa_business_trip_save_draft")
        submit = next(tool for tool in tools if tool["name"] == "oa_business_trip_submit")
        leave_save = next(tool for tool in tools if tool["name"] == "oa_leave_save_draft")
        self.assertTrue(pending["annotations"]["readOnlyHint"])
        self.assertFalse(prepare["annotations"]["readOnlyHint"])
        self.assertFalse(save["annotations"]["readOnlyHint"])
        self.assertFalse(save["annotations"]["destructiveHint"])
        self.assertTrue(submit["annotations"]["destructiveHint"])
        self.assertFalse(leave_save["annotations"]["destructiveHint"])
        approve = next(tool for tool in tools if tool["name"] == "oa_missed_punch_approve")
        prepare_meeting = next(
            tool for tool in tools if tool["name"] == "oa_meeting_create_prepare"
        )
        create_meeting = next(tool for tool in tools if tool["name"] == "oa_meeting_create")
        self.assertTrue(approve["annotations"]["destructiveHint"])
        self.assertTrue(create_meeting["annotations"]["destructiveHint"])
        self.assertNotIn("user_subject", json.dumps(tools))
        self.assertNotIn("expected_principal", json.dumps(tools))
        prepare_schema = prepare["inputSchema"]["properties"]
        self.assertIn("input_submission_id", prepare_schema)
        self.assertNotIn("reason", prepare_schema)
        self.assertNotIn("start_time", prepare_schema)
        meeting_prepare_schema = prepare_meeting["inputSchema"]["properties"]
        for field_name in (
            "subject",
            "room",
            "start_time",
            "end_time",
            "input_submission_id",
        ):
            self.assertIn(field_name, meeting_prepare_schema)
        interaction_get = next(
            tool for tool in tools if tool["name"] == "agentbridge_interaction_get"
        )
        self.assertEqual(
            interaction_get["_meta"]["ui"]["resourceUri"],
            MCP_APP_RESOURCE_URI,
        )

    def test_profile_resource_prompt_and_tool_are_discoverable(self):
        with self._server() as (_service, _store, token, client):
            resources = self._request(
                client,
                "resources/list",
                request_id=20,
                token=token,
            ).json()["result"]["resources"]
            prompts = self._request(
                client,
                "prompts/list",
                request_id=21,
                token=token,
            ).json()["result"]["prompts"]
            profile = self._request(
                client,
                "tools/call",
                request_id=22,
                token=token,
                params={"name": "agentbridge_server_profile", "arguments": {}},
            ).json()["result"]["structuredContent"]
            app_resource = self._request(
                client,
                "resources/read",
                request_id=23,
                token=token,
                params={"uri": MCP_APP_RESOURCE_URI},
            ).json()["result"]["contents"][0]
            operator_prompt = self._request(
                client,
                "prompts/get",
                request_id=24,
                token=token,
                params={"name": "agentbridge_oa_operator", "arguments": {}},
            ).json()["result"]

        resources_by_uri = {item["uri"]: item for item in resources}
        self.assertIn(MCP_PROFILE_RESOURCE_URI, resources_by_uri)
        self.assertEqual(
            resources_by_uri[MCP_APP_RESOURCE_URI]["mimeType"],
            MCP_APP_MIME_TYPE,
        )
        self.assertEqual(profile["mcp"]["endpoint"], "http://testserver/mcp")
        self.assertIn("agentbridge_oa_operator", [item["name"] for item in prompts])
        self.assertEqual(app_resource["mimeType"], MCP_APP_MIME_TYPE)
        self.assertIn("AGENTBRIDGE TRUSTED INTERACTION", app_resource["text"])
        self.assertIn(
            "prepare -> authorize -> commit -> verify",
            operator_prompt["messages"][0]["content"]["text"],
        )

    def test_interaction_card_url_is_private_mcp_result_metadata(self):
        card_url = "https://cards.example.test/input/opaque-resource"
        with self._server() as (service, _store, token, client):
            service.get_interaction.return_value = {
                "protocolVersion": "0.1",
                "interaction": _trusted_interaction(card_url),
            }
            payload = self._request(
                client,
                "tools/call",
                request_id=25,
                token=token,
                params={
                    "name": "agentbridge_interaction_get",
                    "arguments": {"interaction_id": "interaction-1234567890"},
                },
            ).json()["result"]

        model_visible = json.dumps(
            {
                "content": payload["content"],
                "structuredContent": payload["structuredContent"],
            }
        )
        self.assertNotIn(card_url, model_visible)
        self.assertEqual(
            payload["_meta"][PRIVATE_INTERACTION_META_KEY]["presentation"]["url"],
            card_url,
        )

    def test_business_trip_prepare_requires_write_scope_and_uses_server_identity(self):
        with self._server() as (service, store, read_token, client):
            write_identity = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:draft"],
                ttl_seconds=3600,
            )
            service.invoke.return_value = {
                "protocolVersion": "0.1",
                "requestId": "mcp-write",
                "operationId": "prepare-1",
                "status": "requires_user_action",
                "result": None,
                "error": {"code": "WRITE_AUTHORIZATION_REQUIRED", "message": "confirm"},
                "evidenceRefs": [],
                "nextAction": {"cardUrl": "http://127.0.0.1:8780/authorize/card"},
                "reused": False,
            }
            arguments = {"idempotency_key": "mcp-business-trip-prepare"}
            denied = self._request(
                client,
                "tools/call",
                request_id=7,
                token=read_token,
                params={"name": "oa_business_trip_prepare", "arguments": arguments},
            )
            response = self._request(
                client,
                "tools/call",
                request_id=8,
                token=write_identity["token"],
                params={"name": "oa_business_trip_prepare", "arguments": arguments},
            )

        self.assertEqual(denied.status_code, 200)
        self.assertTrue(denied.json()["result"]["isError"])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"]["isError"])
        call = service.invoke.call_args.kwargs
        self.assertEqual(call["user_subject"], "user-a")
        self.assertEqual(call["capability_name"], "oa.business_trip.prepare")
        self.assertEqual(call["idempotency_key"], "mcp-business-trip-prepare")
        self.assertEqual(call["arguments"], {})

    def test_submit_approval_and_meeting_tools_enforce_separate_scopes(self):
        with self._server() as (service, store, read_token, client):
            approval_identity = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:approval"],
                ttl_seconds=3600,
            )
            meeting_identity = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:meeting"],
                ttl_seconds=3600,
            )
            submit_identity = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:submit"],
                ttl_seconds=3600,
            )
            service.invoke.return_value = {
                "protocolVersion": "0.1",
                "requestId": "mcp-write",
                "operationId": "operation-1",
                "status": "succeeded",
                "result": {"submitted_count": 1},
                "error": None,
                "evidenceRefs": [],
                "nextAction": None,
                "reused": False,
            }
            authorization_id = "a" * 32
            read_denied = self._request(
                client,
                "tools/call",
                request_id=31,
                token=read_token,
                params={
                    "name": "oa_missed_punch_approve",
                    "arguments": {"authorization_id": authorization_id},
                },
            )
            approval_allowed = self._request(
                client,
                "tools/call",
                request_id=32,
                token=approval_identity["token"],
                params={
                    "name": "oa_missed_punch_approve",
                    "arguments": {"authorization_id": authorization_id},
                },
            )
            approval_meeting_denied = self._request(
                client,
                "tools/call",
                request_id=33,
                token=approval_identity["token"],
                params={
                    "name": "oa_meeting_create",
                    "arguments": {"authorization_id": authorization_id},
                },
            )
            meeting_allowed = self._request(
                client,
                "tools/call",
                request_id=34,
                token=meeting_identity["token"],
                params={
                    "name": "oa_meeting_create",
                    "arguments": {"authorization_id": authorization_id},
                },
            )
            approval_submit_denied = self._request(
                client,
                "tools/call",
                request_id=35,
                token=approval_identity["token"],
                params={
                    "name": "oa_business_trip_submit",
                    "arguments": {"authorization_id": authorization_id},
                },
            )
            submit_allowed = self._request(
                client,
                "tools/call",
                request_id=36,
                token=submit_identity["token"],
                params={
                    "name": "oa_business_trip_submit",
                    "arguments": {"authorization_id": authorization_id},
                },
            )

        self.assertTrue(read_denied.json()["result"]["isError"])
        self.assertFalse(approval_allowed.json()["result"]["isError"])
        self.assertTrue(approval_meeting_denied.json()["result"]["isError"])
        self.assertFalse(meeting_allowed.json()["result"]["isError"])
        self.assertTrue(approval_submit_denied.json()["result"]["isError"])
        self.assertFalse(submit_allowed.json()["result"]["isError"])
        self.assertEqual(
            [call.kwargs["capability_name"] for call in service.invoke.call_args_list],
            [
                "oa.missed_punch.approve",
                "oa.meeting.create",
                "oa.business_trip.submit",
            ],
        )

    def test_authenticated_tool_uses_server_bound_identity_and_shared_service(self):
        with self._server() as (service, _store, token, client):
            service.invoke.return_value = {
                "protocolVersion": "0.1",
                "requestId": "mcp-request",
                "operationId": "operation-1",
                "status": "succeeded",
                "result": {"collection": "pending", "count": 0, "items": []},
                "error": None,
                "evidenceRefs": [],
                "nextAction": None,
                "reused": False,
            }
            response = self._request(
                client,
                "tools/call",
                request_id=2,
                token=token,
                params={
                    "name": "oa_workflow_pending_list",
                    "arguments": {"limit": 5, "idempotency_key": "mcp-pending-1"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["result"]["isError"])
        self.assertEqual(payload["result"]["structuredContent"]["status"], "succeeded")
        call = service.invoke.call_args.kwargs
        self.assertEqual(call["user_subject"], "user-a")
        self.assertEqual(call["capability_name"], "oa.workflow.pending.list")
        self.assertEqual(call["arguments"], {"limit": 5})
        self.assertEqual(call["idempotency_key"], "mcp-pending-1")

    def test_login_card_uses_identity_bound_expected_principal(self):
        with self._server() as (service, _store, token, client):
            service.start_login.return_value = {
                "protocolVersion": "0.1",
                "status": "requires_user_action",
                "nextAction": {"cardUrl": "http://127.0.0.1:8780/auth/challenge"},
            }
            response = self._request(
                client,
                "tools/call",
                request_id=3,
                token=token,
                params={
                    "name": "oa_session_login",
                    "arguments": {"challenge_ttl_seconds": 600},
                },
            )

        self.assertEqual(response.status_code, 200)
        call = service.start_login.call_args.kwargs
        self.assertEqual(call["user_subject"], "user-a")
        self.assertEqual(call["expected_principal_ref"], "Alice")
        self.assertEqual(call["card_base_url"], "http://127.0.0.1:8780")
        self.assertEqual(call["ttl_seconds"], 600)

    def test_interaction_resume_requires_write_scope_for_business_input(self):
        with self._server() as (service, store, read_token, client):
            write_identity = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:draft"],
                ttl_seconds=3600,
            )
            service.interaction_required_scopes.return_value = frozenset(
                {"oa:write:draft"}
            )
            service.get_interaction.return_value = {
                "protocolVersion": "0.1",
                "interaction": {
                    "interactionId": "interaction-123456",
                    "type": "business_input",
                    "state": "completed",
                    "resume": {"ready": True, "completed": False},
                },
            }
            service.resume_interaction.return_value = {
                "protocolVersion": "0.1",
                "status": "requires_user_action",
                "resumedFromInteractionId": "interaction-123456",
            }
            denied = self._request(
                client,
                "tools/call",
                request_id=9,
                token=read_token,
                params={
                    "name": "agentbridge_interaction_resume",
                    "arguments": {"interaction_id": "interaction-123456"},
                },
            )
            response = self._request(
                client,
                "tools/call",
                request_id=10,
                token=write_identity["token"],
                params={
                    "name": "agentbridge_interaction_resume",
                    "arguments": {
                        "interaction_id": "interaction-123456",
                        "idempotency_key": "resume-1",
                    },
                },
            )

        self.assertTrue(denied.json()["result"]["isError"])
        self.assertFalse(response.json()["result"]["isError"])
        service.resume_interaction.assert_called_once_with(
            user_subject="user-a",
            interaction_id="interaction-123456",
            idempotency_key="resume-1",
        )

    def test_revoked_token_is_rejected_without_calling_service(self):
        with self._server() as (service, store, token, client):
            record = store.verify(token)
            store.revoke(record["token_id"])
            response = self._request(
                client,
                "tools/call",
                request_id=4,
                token=token,
                params={"name": "oa_session_status", "arguments": {}},
            )

        self.assertEqual(response.status_code, 401)
        service.session_status.assert_not_called()

    def test_each_bearer_token_routes_to_its_own_server_bound_user(self):
        with self._server() as (service, store, _token, client):
            second = store.issue(
                user_subject="user-b",
                expected_principal_ref="Bob",
                ttl_seconds=3600,
            )
            service.session_status.return_value = {
                "protocolVersion": "0.1",
                "status": "not_found",
                "systemId": "oa",
                "userSubject": "user-b",
            }
            response = self._request(
                client,
                "tools/call",
                request_id=6,
                token=second["token"],
                params={"name": "oa_session_status", "arguments": {}},
            )

        self.assertEqual(response.status_code, 200)
        service.session_status.assert_called_once_with(
            user_subject="user-b",
            system_id="oa",
        )

    def test_mcp_and_direct_service_share_idempotent_operation(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            service = CentralCapabilityService(
                home=home,
                base_url="http://oa.example.test/seeyon/main.do?method=main",
            )
            store = McpIdentityTokenStore(home / "agentbridge.db")
            issued = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                ttl_seconds=3600,
            )
            config = validate_central_mcp_server_config(
                host="127.0.0.1",
                port=8790,
                public_base_url="http://testserver",
                tls_cert=None,
                tls_key=None,
            )
            server = create_central_mcp_server(
                service=service,
                identity_store=store,
                config=config,
                auth_card_base_url="http://127.0.0.1:8780",
            )
            with TestClient(server.streamable_http_app()) as client:
                mcp_response = self._request(
                    client,
                    "tools/call",
                    request_id=5,
                    token=issued["token"],
                    params={
                        "name": "oa_template_list",
                        "arguments": {"idempotency_key": "shared-operation"},
                    },
                ).json()["result"]["structuredContent"]

            direct_response = service.invoke(
                user_subject="user-a",
                capability_name="oa.template.list",
                arguments={},
                idempotency_key="shared-operation",
            )

        self.assertEqual(mcp_response["status"], "requires_user_action")
        self.assertEqual(direct_response["operationId"], mcp_response["operationId"])
        self.assertTrue(direct_response["reused"])

    def test_runtime_starts_and_stops_auth_card_with_mcp_server(self):
        service = MagicMock()
        identity_store = MagicMock()
        mcp_config = validate_central_mcp_server_config(
            host="127.0.0.1",
            port=8790,
            public_base_url=None,
            tls_cert=None,
            tls_key=None,
        )
        auth_config = AuthServerConfig(
            host="127.0.0.1",
            port=8780,
            public_base_url="http://127.0.0.1:8780",
            tls_cert=None,
            tls_key=None,
        )
        auth_server = MagicMock()
        mcp = MagicMock()
        app = object()
        mcp.streamable_http_app.return_value = app

        with (
            patch("bscli.mcp.central.create_auth_http_server", return_value=auth_server),
            patch("bscli.mcp.central.create_central_mcp_server", return_value=mcp),
            patch("bscli.mcp.central.CentralSessionKeepalive") as keepalive_class,
            patch("bscli.mcp.central.uvicorn.run") as run,
        ):
            serve_central_mcp(
                service=service,
                identity_store=identity_store,
                mcp_config=mcp_config,
                auth_config=auth_config,
            )

        auth_server.serve_forever.assert_called_once_with(poll_interval=0.25)
        keepalive_class.return_value.start.assert_called_once_with()
        keepalive_class.return_value.stop.assert_called_once_with()
        run.assert_called_once()
        self.assertIs(run.call_args.args[0], app)
        auth_server.shutdown.assert_called_once()
        auth_server.server_close.assert_called_once()

    def test_runtime_closes_auth_card_if_mcp_initialization_fails(self):
        service = MagicMock()
        mcp_config = validate_central_mcp_server_config(
            host="127.0.0.1",
            port=8790,
            public_base_url=None,
            tls_cert=None,
            tls_key=None,
        )
        auth_config = AuthServerConfig(
            host="127.0.0.1",
            port=8780,
            public_base_url="http://127.0.0.1:8780",
            tls_cert=None,
            tls_key=None,
        )
        auth_server = MagicMock()

        with (
            patch("bscli.mcp.central.create_auth_http_server", return_value=auth_server),
            patch(
                "bscli.mcp.central.create_central_mcp_server",
                side_effect=RuntimeError("MCP setup failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "MCP setup failed"),
        ):
            serve_central_mcp(
                service=service,
                identity_store=MagicMock(),
                mcp_config=mcp_config,
                auth_config=auth_config,
            )

        auth_server.shutdown.assert_called_once()
        auth_server.server_close.assert_called_once()

    def _server(self):
        return CentralMcpFixture()

    @staticmethod
    def _request(
        client,
        method,
        *,
        request_id,
        params=None,
        token=None,
        authenticated=True,
    ):
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {token}"
        return client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
        )


class CentralMcpFixture:
    def __enter__(self):
        self.temp = TemporaryDirectory()
        self.service = MagicMock()
        self.store = McpIdentityTokenStore(Path(self.temp.name) / "agentbridge.db")
        issued = self.store.issue(
            user_subject="user-a",
            expected_principal_ref="Alice",
            label="test-client",
            ttl_seconds=3600,
        )
        self.token = issued["token"]
        config = validate_central_mcp_server_config(
            host="127.0.0.1",
            port=8790,
            public_base_url="http://testserver",
            tls_cert=None,
            tls_key=None,
        )
        self.server = create_central_mcp_server(
            service=self.service,
            identity_store=self.store,
            config=config,
            auth_card_base_url="http://127.0.0.1:8780",
        )
        self.client_context = TestClient(self.server.streamable_http_app())
        self.client = self.client_context.__enter__()
        return self.service, self.store, self.token, self.client

    def __exit__(self, exc_type, exc, traceback):
        self.client_context.__exit__(exc_type, exc, traceback)
        self.temp.cleanup()


def _trusted_interaction(card_url):
    return {
        "schemaVersion": "agentbridge.interaction.v1",
        "interactionId": "interaction-1234567890",
        "type": "business_input",
        "state": "pending",
        "title": "Business trip input",
        "message": "Enter the requested business fields.",
        "presentation": {
            "owner": "agentbridge",
            "preferred": "embedded_secure_web_app",
            "fallback": "url",
            "url": card_url,
            "modelMustNotCollectValues": True,
        },
        "display": {"systemName": "OA", "fieldCount": 6},
        "poll": {
            "tool": "agentbridge_interaction_get",
            "recommendedIntervalSeconds": 2,
        },
        "resume": {
            "tool": "agentbridge_interaction_resume",
            "ready": False,
            "completed": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
