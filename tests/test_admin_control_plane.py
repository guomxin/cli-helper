from __future__ import annotations

from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import socket
import sqlite3
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bscli.admin.application import AdminControlPlane
from bscli.admin.server import create_admin_http_server, validate_admin_server_config
from bscli.admin.stores import (
    AdminAccountStore,
    AdminAuditStore,
    AdminSessionStore,
    GovernancePolicyDenied,
    GovernancePolicyStore,
)
from bscli.core.capability import CapabilityRegistry, CapabilitySpec
from bscli.core.central_service import CentralCapabilityService
from bscli.cli.main import main
from bscli.core.mcp_identities import McpIdentityTokenStore


PASSWORD = "AgentBridge!Admin9"
NEW_PASSWORD = "AgentBridge!Admin10"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class AdminStoreTests(unittest.TestCase):
    def test_password_is_hashed_and_first_change_is_enforced(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agentbridge.db"
            store = AdminAccountStore(db_path)
            account = store.create(username="admin", password=PASSWORD)

            self.assertTrue(account["must_change_password"])
            self.assertIsNotNone(store.authenticate(username="ADMIN", password=PASSWORD))
            self.assertIsNone(store.authenticate(username="admin", password="wrong-password"))
            with closing(sqlite3.connect(db_path)) as connection:
                encoded = connection.execute(
                    "SELECT password_hash FROM admin_accounts WHERE account_id = ?",
                    (account["account_id"],),
                ).fetchone()[0]
            self.assertNotIn(PASSWORD, encoded)
            self.assertTrue(encoded.startswith("scrypt$"))

            changed = store.change_password(
                account_id=account["account_id"],
                current_password=PASSWORD,
                new_password=NEW_PASSWORD,
            )
            self.assertFalse(changed["must_change_password"])
            self.assertIsNone(store.authenticate(username="admin", password=PASSWORD))
            self.assertIsNotNone(store.authenticate(username="admin", password=NEW_PASSWORD))
            with self.assertRaises(ValueError):
                store.change_password(
                    account_id=account["account_id"],
                    current_password=NEW_PASSWORD,
                    new_password=NEW_PASSWORD,
                )

    def test_admin_session_requires_matching_csrf_and_honors_idle_timeout(self) -> None:
        clock = MutableClock()
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agentbridge.db"
            account = AdminAccountStore(db_path, clock=clock).create(
                username="admin", password=PASSWORD, must_change_password=False
            )
            sessions = AdminSessionStore(
                db_path,
                clock=clock,
                ttl_seconds=600,
                idle_seconds=120,
            )
            session = sessions.create(
                account_id=account["account_id"],
                request_ip="127.0.0.1",
                user_agent="test",
            )
            self.assertIsNotNone(sessions.verify(session["token"]))
            self.assertIsNone(sessions.verify(session["token"], csrf_token="wrong"))
            self.assertIsNotNone(
                sessions.verify(session["token"], csrf_token=session["csrf_token"])
            )
            clock.value += timedelta(seconds=121)
            self.assertIsNone(sessions.verify(session["token"]))

    def test_admin_audit_is_append_only(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agentbridge.db"
            store = AdminAuditStore(db_path)
            event = store.append(
                actor={"account_id": "a", "username": "admin", "role": "admin"},
                action="test.action",
                target_type="test",
                target_id="one",
                reason="unit test",
                result="succeeded",
                before={"state": "before"},
                after={"state": "after"},
            )
            self.assertEqual(event["after"], {"state": "after"})
            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE admin_audit_events SET result = 'changed' WHERE event_id = ?",
                        (event["event_id"],),
                    )
                connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "DELETE FROM admin_audit_events WHERE event_id = ?",
                        (event["event_id"],),
                    )

    def test_governance_policy_matches_global_system_user_and_capability(self) -> None:
        with TemporaryDirectory() as tmp:
            store = GovernancePolicyStore(Path(tmp) / "agentbridge.db")
            system = store.pause(
                scope_type="system",
                scope_value="oa",
                reason="maintenance window",
                actor="admin",
            )
            with self.assertRaises(GovernancePolicyDenied) as caught:
                store.assert_write_allowed(
                    system_id="oa",
                    user_subject="user-a",
                    capability_name="oa.leave.submit",
                    capability_version="1.0.0",
                )
            self.assertEqual(caught.exception.policy["policy_id"], system["policy_id"])
            store.resume(system["policy_id"], reason="maintenance complete", actor="admin")
            store.assert_write_allowed(
                system_id="oa",
                user_subject="user-a",
                capability_name="oa.leave.submit",
                capability_version="1.0.0",
            )


class GovernanceRuntimeTests(unittest.TestCase):
    def test_paused_write_is_recorded_as_write_paused_before_downstream_login(self) -> None:
        registry = CapabilityRegistry()
        registry.register(
            CapabilitySpec(
                name="oa.test.write",
                version="1.0.0",
                description="test write",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                effect="controlled_write",
                adapter="seeyon-central",
                workflow="test",
            )
        )
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
                registry=registry,
            )
            service.governance_policies.pause(
                scope_type="global",
                scope_value="*",
                reason="emergency stop",
                actor="admin",
            )
            result = service.invoke(
                user_subject="user-a",
                capability_name="oa.test.write",
                arguments={},
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "WRITE_PAUSED")
            operation = service.operations.get(result["operationId"])
            self.assertEqual(operation["status"], "failed")


class AdminControlPlaneTests(unittest.TestCase):
    def test_runtime_exposes_non_sensitive_coordination_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
            )
            identities = McpIdentityTokenStore(service.db_path)
            issued = identities.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read"],
                ttl_seconds=3600,
            )
            service.tasks.ensure_endpoint(
                user_subject="user-a",
                token_id=issued["token_id"],
                agent_host="openclaw",
                endpoint_key="telegram:*:sensitive-peer-uat-9f4c",
                client_type="telegram",
                external_subject="sensitive-peer-uat-9f4c",
                conversation_ref=(
                    "agent:main:telegram:direct:sensitive-peer-uat-9f4c"
                ),
            )

            runtime = AdminControlPlane(
                service=service,
                identity_store=identities,
            ).runtime()

        task_hub = runtime["coordination"]["task_hub"]
        self.assertTrue(task_hub["isolation"]["passed"])
        self.assertEqual(task_hub["summary"]["active_endpoints"], 1)
        self.assertEqual(task_hub["users"][0]["user_subject"], "user-a")
        self.assertNotIn("sensitive-peer-uat-9f4c", json.dumps(task_hub))
        self.assertIn("operations", runtime["coordination"]["host_control"])

    def test_token_issue_adds_base_read_scope_and_identity_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
                taihua_base_url="http://127.0.0.1:8001",
            )
            identities = McpIdentityTokenStore(service.db_path)
            control = AdminControlPlane(service=service, identity_store=identities)
            actor = {"account_id": "a", "username": "admin", "role": "admin"}
            issued = control.issue_token(
                actor=actor,
                request_ip="127.0.0.1",
                user_subject="user-a",
                expected_principal_ref="principal-a",
                label="OpenClaw",
                scopes=["oa:write:submit", "taihua:write:worklog"],
                ttl_hours=24,
                reason="client onboarding",
            )

            self.assertTrue(issued["token_secret"].startswith("abmcp_"))
            self.assertEqual(
                set(issued["scopes"]),
                {"oa:read", "oa:write:submit", "taihua:read", "taihua:write:worklog"},
            )
            self.assertIsNotNone(service.sessions.find(user_subject="user-a", system_id="oa"))
            self.assertIsNotNone(service.sessions.find(user_subject="user-a", system_id="taihua"))
            self.assertNotIn("token_secret", control.list_tokens()[0])
            self.assertNotIn(issued["token_secret"], json.dumps(control.audit.list()))

    def test_token_issue_supports_distinct_system_principal_bindings(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
                taihua_base_url="http://127.0.0.1:8001",
            )
            control = AdminControlPlane(
                service=service,
                identity_store=McpIdentityTokenStore(service.db_path),
            )
            issued = control.issue_token(
                actor={"account_id": "a", "username": "admin", "role": "admin"},
                request_ip="127.0.0.1",
                user_subject="user-a",
                expected_principal_ref=None,
                principal_bindings={
                    "oa": "Alice OA",
                    "taihua": "alice.worklog",
                },
                label="OpenClaw",
                scopes=["oa:read", "taihua:read"],
                ttl_hours=24,
                reason="multi-system onboarding",
            )

            self.assertEqual(
                issued["principal_bindings"],
                {"oa": "Alice OA", "taihua": "alice.worklog"},
            )
            self.assertEqual(issued["expected_principal_ref"], "Alice OA")
            self.assertEqual(
                service.sessions.find(
                    user_subject="user-a", system_id="oa"
                )["expected_principal_ref"],
                "Alice OA",
            )
            self.assertEqual(
                service.sessions.find(
                    user_subject="user-a", system_id="taihua"
                )["expected_principal_ref"],
                "alice.worklog",
            )
            user = control.users()[0]
            self.assertEqual(
                user["principal_bindings"],
                {
                    "oa": {"expected": "Alice OA", "verified": None},
                    "taihua": {"expected": "alice.worklog", "verified": None},
                },
            )

            rotated = control.issue_token(
                actor={"account_id": "a", "username": "admin", "role": "admin"},
                request_ip="127.0.0.1",
                user_subject="user-a",
                expected_principal_ref=None,
                principal_bindings={},
                label="OpenClaw rotated",
                scopes=["oa:read", "taihua:read"],
                ttl_hours=24,
                reason="credential rotation",
            )
            self.assertEqual(rotated["principal_bindings"], issued["principal_bindings"])

    def test_admin_rebinds_only_one_system_and_clears_its_login_state(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
                taihua_base_url="http://127.0.0.1:8001",
            )
            control = AdminControlPlane(
                service=service,
                identity_store=McpIdentityTokenStore(service.db_path),
            )
            actor = {"account_id": "a", "username": "admin", "role": "admin"}
            control.issue_token(
                actor=actor,
                request_ip="127.0.0.1",
                user_subject="user-a",
                expected_principal_ref=None,
                principal_bindings={"oa": "Alice OA", "taihua": "alice.worklog"},
                label="OpenClaw",
                scopes=["oa:read", "taihua:read"],
                ttl_hours=24,
                reason="onboarding",
            )
            oa = service.sessions.find(user_subject="user-a", system_id="oa")
            taihua = service.sessions.find(user_subject="user-a", system_id="taihua")
            service.sessions.activate(oa["session_id"], observed_principal_ref="Alice OA")
            service.sessions.activate(
                taihua["session_id"], observed_principal_ref="alice.worklog"
            )
            service.session_states.save(
                taihua["session_id"], {"cookies": [{"owner": "user-a"}]}
            )

            rebound = control.rebind_session_principal(
                actor=actor,
                request_ip="127.0.0.1",
                session_id=taihua["session_id"],
                expected_principal_ref="alice-new",
                reason="account renamed",
            )

            self.assertEqual(rebound["expected_principal_ref"], "alice-new")
            self.assertEqual(rebound["state"], "expired")
            self.assertIsNone(rebound["downstream_principal_ref"])
            self.assertIsNone(service.session_states.load(taihua["session_id"]))
            self.assertEqual(service.sessions.get(oa["session_id"])["state"], "active")
            self.assertEqual(control.audit.list()[0]["action"], "session.principal.rebind")

    def test_unknown_scope_is_rejected_before_identity_sessions_are_created(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
            )
            control = AdminControlPlane(
                service=service,
                identity_store=McpIdentityTokenStore(service.db_path),
            )
            with self.assertRaises(ValueError):
                control.issue_token(
                    actor={"account_id": "a", "username": "admin", "role": "admin"},
                    request_ip="127.0.0.1",
                    user_subject="user-a",
                    expected_principal_ref="principal-a",
                    label="invalid",
                    scopes=["oa:write:unknown"],
                    ttl_hours=24,
                    reason="unit test",
                )

            self.assertIsNone(
                service.sessions.find(user_subject="user-a", system_id="oa")
            )
    def test_auditor_cannot_execute_control_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
            )
            control = AdminControlPlane(
                service=service,
                identity_store=McpIdentityTokenStore(service.db_path),
            )
            with self.assertRaises(PermissionError):
                control.pause_policy(
                    actor={"account_id": "b", "username": "audit", "role": "auditor"},
                    request_ip="127.0.0.1",
                    scope_type="global",
                    scope_value="*",
                    capability_version="*",
                    reason="not allowed",
                )


class AdminHttpServerTests(unittest.TestCase):
    def test_login_cookies_csrf_and_no_secret_retrieval(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
            )
            identities = McpIdentityTokenStore(service.db_path)
            control = AdminControlPlane(service=service, identity_store=identities)
            account = control.accounts.create(
                username="admin",
                password=PASSWORD,
                must_change_password=False,
            )
            self.assertEqual(account["role"], "admin")
            port = _free_port()
            origin = f"http://127.0.0.1:{port}"
            config = validate_admin_server_config(
                host="127.0.0.1",
                port=port,
                public_base_url=origin,
                tls_cert=None,
                tls_key=None,
            )
            server = create_admin_http_server(config=config, control_plane=control)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, body = _request(
                    port,
                    "POST",
                    "/api/login",
                    body={"username": "admin", "password": PASSWORD},
                    origin=origin,
                )
                self.assertEqual(status, 200)
                cookies = _cookies(headers)
                self.assertIn("agentbridge_admin_session", cookies)
                self.assertIn("agentbridge_admin_csrf", cookies)
                self.assertIn("HttpOnly", " ".join(headers.get_all("Set-Cookie") or []))

                status, response_headers, session = _request(
                    port,
                    "GET",
                    "/api/session",
                    cookies=cookies,
                )
                self.assertEqual(status, 200)
                self.assertEqual(session["account"]["username"], "admin")
                self.assertEqual(response_headers.get("X-Frame-Options"), "DENY")

                status, _, issued = _request(
                    port,
                    "POST",
                    "/api/tokens",
                    body={
                        "user_subject": "user-a",
                        "expected_principal_ref": "principal-a",
                        "label": "test",
                        "scopes": ["oa:read"],
                        "ttl_hours": 24,
                        "reason": "server test",
                    },
                    origin=origin,
                    cookies=cookies,
                    csrf=cookies["agentbridge_admin_csrf"],
                )
                self.assertEqual(status, 201)
                self.assertTrue(issued["token_secret"].startswith("abmcp_"))

                status, _, listed = _request(
                    port,
                    "GET",
                    "/api/tokens",
                    cookies=cookies,
                )
                self.assertEqual(status, 200)
                self.assertNotIn("token_secret", listed["items"][0])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class AdminBootstrapCliTests(unittest.TestCase):
    def test_bootstrap_password_is_read_from_stdin_and_never_printed(self) -> None:
        with TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as stdout:
            with patch("sys.stdin", io.StringIO(PASSWORD + "\n")):
                exit_code = main(
                    [
                        "--home",
                        tmp,
                        "admin",
                        "account",
                        "bootstrap",
                        "--username",
                        "admin",
                        "--password-stdin",
                    ]
                )
            output = stdout.getvalue()

            self.assertEqual(exit_code, 0)
            self.assertNotIn(PASSWORD, output)
            payload = json.loads(output)
            self.assertTrue(payload["account"]["must_change_password"])
            self.assertEqual(payload["account"]["role"], "admin")
            self.assertEqual(AdminAccountStore(Path(tmp) / "agentbridge.db").count(), 1)


class AdminFirstLoginHttpTests(unittest.TestCase):
    def test_bootstrap_account_must_change_password_before_overview(self) -> None:
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://127.0.0.1:8000/seeyon",
            )
            control = AdminControlPlane(
                service=service,
                identity_store=McpIdentityTokenStore(service.db_path),
            )
            control.accounts.create(username="admin", password=PASSWORD)
            port = _free_port()
            origin = f"http://127.0.0.1:{port}"
            server = create_admin_http_server(
                config=validate_admin_server_config(
                    host="127.0.0.1",
                    port=port,
                    public_base_url=origin,
                    tls_cert=None,
                    tls_key=None,
                ),
                control_plane=control,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, _ = _request(
                    port,
                    "POST",
                    "/api/login",
                    body={"username": "admin", "password": PASSWORD},
                    origin=origin,
                )
                self.assertEqual(status, 200)
                cookies = _cookies(headers)
                status, _, error = _request(port, "GET", "/api/overview", cookies=cookies)
                self.assertEqual(status, 403)
                self.assertEqual(error["error"]["code"], "PASSWORD_CHANGE_REQUIRED")

                status, _, changed = _request(
                    port,
                    "POST",
                    "/api/account/password",
                    body={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                    origin=origin,
                    cookies=cookies,
                    csrf=cookies["agentbridge_admin_csrf"],
                )
                self.assertEqual(status, 200)
                self.assertFalse(changed["account"]["must_change_password"])
                status, _, overview = _request(port, "GET", "/api/overview", cookies=cookies)
                self.assertEqual(status, 200)
                self.assertIn("summary", overview)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class AdminStaticAssetTests(unittest.TestCase):
    def test_login_form_survives_async_submit_and_assets_are_csp_clean(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "bscli/admin/static/admin.js").read_text(encoding="utf-8")
        page = (root / "bscli/admin/static/index.html").read_text(encoding="utf-8")
        stylesheet = (root / "bscli/admin/static/admin.css").read_text(encoding="utf-8")

        self.assertIn("const loginForm = event.currentTarget;", script)
        self.assertIn("loginForm.reset();", script)
        self.assertNotIn("event.currentTarget.reset()", script)
        self.assertNotIn('style="', page)
        self.assertNotIn("font-size: clamp(", stylesheet)
        self.assertNotIn("style='", page)


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
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers = {"Host": f"127.0.0.1:{port}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if cookies:
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    if csrf:
        headers["X-AgentBridge-CSRF"] = csrf
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, response.headers, parsed
    finally:
        connection.close()


def _cookies(headers: http.client.HTTPMessage) -> dict[str, str]:
    result = {}
    for value in headers.get_all("Set-Cookie") or []:
        pair = value.split(";", 1)[0]
        name, cookie_value = pair.split("=", 1)
        result[name] = cookie_value
    return result


if __name__ == "__main__":
    unittest.main()
