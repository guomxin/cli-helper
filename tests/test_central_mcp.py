import json
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
    create_central_mcp_server,
    serve_central_mcp,
    validate_central_mcp_server_config,
)


class CentralMcpTests(unittest.TestCase):
    def test_non_loopback_server_requires_https_and_tls(self):
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            validate_central_mcp_server_config(
                host="0.0.0.0",
                port=8790,
                public_base_url="http://mcp.example.test",
                tls_cert=None,
                tls_key=None,
            )

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
        self.assertIn("oa_business_trip_prepare", names)
        self.assertIn("oa_business_trip_save_draft", names)
        pending = next(tool for tool in tools if tool["name"] == "oa_workflow_pending_list")
        prepare = next(tool for tool in tools if tool["name"] == "oa_business_trip_prepare")
        save = next(tool for tool in tools if tool["name"] == "oa_business_trip_save_draft")
        self.assertTrue(pending["annotations"]["readOnlyHint"])
        self.assertFalse(prepare["annotations"]["readOnlyHint"])
        self.assertFalse(save["annotations"]["readOnlyHint"])
        self.assertFalse(save["annotations"]["destructiveHint"])
        self.assertNotIn("user_subject", json.dumps(tools))
        self.assertNotIn("expected_principal", json.dumps(tools))

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
            arguments = {
                "start_time": "2026-07-13 09:00",
                "end_time": "2026-07-13 18:00",
                "travel_mode": "火车",
                "origin": "济南",
                "destination": "青岛",
                "reason": "Test",
                "has_direct_supervisor": False,
                "idempotency_key": "mcp-business-trip-prepare",
            }
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
        self.assertNotIn("idempotency_key", call["arguments"])

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
            patch("bscli.mcp.central.uvicorn.run") as run,
        ):
            serve_central_mcp(
                service=service,
                identity_store=identity_store,
                mcp_config=mcp_config,
                auth_config=auth_config,
            )

        auth_server.serve_forever.assert_called_once_with(poll_interval=0.25)
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


if __name__ == "__main__":
    unittest.main()
