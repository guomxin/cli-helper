import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from bscli.analytics.comparison import compare, COMPARE
from bscli.analytics.contracts import SCOPES, SUMMARY, RESULT_GET, metrics
from bscli.analytics.reports import EXPORT, DOWNLOAD, EXPORT_SCOPES, render_csv
from bscli.core.task_plan_validation import PlanValidationError
from tests import test_taihua_analytics as fixtures
from tests.test_taihua_analytics import Assertions, ARGS


class PhaseBTests(Assertions):
    setUp = fixtures.CentralTests.setUp
    tearDown = fixtures.CentralTests.tearDown
    invoke = fixtures.CentralTests.invoke
    task = fixtures.CentralTests.task

    def permit_export(self):
        self.service.set_task_plan_authority_resolver(lambda token, scopes:
            {"user_subject": "self"} if self.allowed and token == "token" and scopes.issubset(EXPORT_SCOPES) else {})

    def summary(self):
        response = self.invoke()
        self.assertEqual(response["status"], "succeeded", response)
        return response["result"]

    def proposal(self):
        return {"schemaVersion": "agentbridge.task-plan.proposal.v2", "goal": "比较本人两个日报窗口", "constraints": {},
                "steps": [
                    {"stepKey": key, "kind": "capability", "capabilityName": SUMMARY, "arguments": dict(ARGS)}
                    for key in ("baseline", "current")
                ] + [{"stepKey": "compare", "kind": "transform", "transformName": COMPARE,
                      "dependsOn": ["baseline", "current"], "arguments": {"allow_unequal_windows": False},
                      "bindings": {key: {"mode": "single", "step": key, "pointer": ""} for key in ("baseline", "current")}}]}

    def prepare(self, proposal=None):
        return self.service.prepare_task_plan(user_subject="self", task_id=self.task(),
            proposal=proposal or self.proposal(), granted_scopes=SCOPES,
            authority_identity={"token_id": "token", "user_subject": "self", "scopes": list(SCOPES)})

    def test_plan_persists_only_references_and_get_rechecks_both_sources(self):
        response = self.prepare()
        self.assertEqual(response["status"], "succeeded", response)
        projection = response["plan"]["resultProjection"]
        self.assertEqual(projection["kind"], "protected_analytics")
        self.assertNotIn('"registered_hours"', json.dumps(response))
        rid = projection["result"]["result_id"]
        result = self.invoke(RESULT_GET, {"result_id": rid}, task_id=response["plan"]["taskId"], idempotency_key="plan-result")
        self.assertEqual(result["status"], "succeeded", result)
        replay = self.invoke(RESULT_GET, {"result_id": rid}, task_id=response["plan"]["taskId"], idempotency_key="plan-result")
        self.assertEqual(replay["status"], "succeeded", replay)
        self.assertEqual(result["result"]["comparison"]["registered_hours"]["delta"], "0.0000")
        self.worker.rows = []
        denied = self.invoke(RESULT_GET, {"result_id": rid})
        self.assertEqual(denied["error"]["code"], "RESULT_ACCESS_REVOKED")

    def test_analytics_plan_rejects_fabricated_references_and_extra_steps(self):
        proposal = self.proposal()
        proposal["steps"][-1]["bindings"]["current"]["step"] = "baseline"
        with self.assertRaises(PlanValidationError):
            self.prepare(proposal)
        self.assertEqual(self.connection.calls, [])

    def test_registry_cannot_resolve_protected_values(self):
        from bscli.core.transforms import TransformRejected
        ref = {"result_id": "a"*32, "protected": True, "schema_version": "taihua.analytics.reference.v1"}
        with self.assertRaises(TransformRejected):
            self.service.transforms.invoke(COMPARE, {"baseline": ref, "current": ref, "allow_unequal_windows": False})

    def test_zero_baseline_and_unequal_windows(self):
        baseline = self.summary()
        current = deepcopy(baseline)
        baseline["metrics"] = metrics([])
        out = compare(baseline, current)
        self.assertIsNone(out["comparison"]["registered_hours"]["change_rate"])
        self.assertEqual(out["comparison"]["registered_hours"]["change_rate_note"], "基期为零")
        current["window"]["end_date_exclusive"] = "2026-09-03"
        self.error("INVALID_ANALYSIS_INPUT", lambda: compare(baseline, current))
        out = compare(baseline, current, allow_unequal_windows=True)
        self.assertNotIn("registered_hours", out["comparison"])
        self.assertIn("registered_hours_per_calendar_day", out["comparison"])
        current["provenance"]["policy_version"] = "different"
        self.error("DATA_CONTRACT_CHANGED", lambda: compare(baseline, current, allow_unequal_windows=True))

    def test_export_download_auth_and_no_plaintext_operation(self):
        self.permit_export()
        source = self.summary()
        report = self.invoke(EXPORT, {"result_id": source["result_id"]})
        self.assertEqual(report["status"], "succeeded", report)
        rid = report["result"]["report_id"]
        download = self.invoke(DOWNLOAD, {"report_id": rid}, idempotency_key="download")
        self.assertEqual(download["status"], "succeeded", download)
        csv = base64.b64decode(download["result"]["file"]["content_base64"]).decode("utf-8-sig")
        self.assertIn("登记工时", csv)
        self.assertNotIn("content_base64", json.dumps(self.service.operations.get(download["operationId"])))
        self.assertNotIn("SYNTHETIC", csv)
        self.allowed = False
        self.assertEqual(self.invoke(DOWNLOAD, {"report_id": rid}, idempotency_key="download")["error"]["code"], "DATA_ACCESS_DENIED")

    def test_export_scope_is_separate_and_current_visibility_required(self):
        source = self.summary()
        self.assertEqual(self.invoke(EXPORT, {"result_id": source["result_id"]})["error"]["code"], "DATA_ACCESS_DENIED")
        self.permit_export()
        report = self.invoke(EXPORT, {"result_id": source["result_id"]})["result"]
        self.worker.rows = []
        self.assertEqual(self.invoke(DOWNLOAD, {"report_id": report["report_id"]})["error"]["code"], "RESULT_ACCESS_REVOKED")

    def test_report_expiry_owner_and_csv_escaping(self):
        self.permit_export()
        source = self.summary()
        source["series"][0]["date"] = "=HYPERLINK(1)"
        self.assertIn("'=HYPERLINK(1)", render_csv(source).decode("utf-8-sig"))
        report = self.invoke(EXPORT, {"result_id": source["result_id"]})["result"]
        rid = report["report_id"]
        self.error("DATA_ACCESS_DENIED", lambda: self.runtime.results.load(rid, owner="other"))
        payload = self.runtime.results.load(rid, owner="self")
        payload["download_expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.runtime.results.payloads.save(rid, payload)
        self.assertEqual(self.invoke(DOWNLOAD, {"report_id": rid})["error"]["code"], "RESULT_EXPIRED")

    def test_aggregate_only_result_cannot_be_exported(self):
        self.permit_export()
        source = self.invoke(arguments={**ARGS, "group_by": "none"})["result"]
        self.assertEqual(self.invoke(EXPORT, {"result_id": source["result_id"]})["error"]["code"], "INVALID_ANALYSIS_INPUT")

    def test_nested_owner_id_supported_but_conflicting_id_rejected(self):
        from bscli.analytics.visibility import projection
        from bscli.analytics.contracts import Query
        row = {**fixtures.ROW, "userId": None, "user": {"id": 7}}
        projection(row, Query.parse(ARGS), owner="7")
        self.error("COVERAGE_UNVERIFIABLE", lambda: projection({**row, "userId": 9}, Query.parse(ARGS), owner="7"))
        self.error("COVERAGE_UNVERIFIABLE", lambda: projection({**row, "user": {}}, Query.parse(ARGS), owner="7"))

    def test_canceled_task_cannot_retrieve_result(self):
        source = self.summary()
        task = self.task()
        self.service.tasks.cancel_task(task_id=task, user_subject="self", reason="test")
        denied = self.invoke(RESULT_GET, {"result_id": source["result_id"]}, task_id=task)
        self.assertEqual(denied["error"]["code"], "DATA_ACCESS_DENIED")

    def test_authenticated_mcp_export_and_download(self):
        from starlette.testclient import TestClient
        from bscli.core.mcp_identities import McpIdentityTokenStore
        from bscli.mcp.central import create_central_mcp_server, validate_central_mcp_server_config
        source = self.summary()
        store = McpIdentityTokenStore(self.service.db_path)
        tokens = [store.issue(user_subject="self", expected_principal_ref="测试本人", scopes=list(scopes))
                  for scopes in (SCOPES, EXPORT_SCOPES)]
        server = create_central_mcp_server(service=self.service, identity_store=store,
            config=validate_central_mcp_server_config(host="127.0.0.1", port=8790, public_base_url="http://testserver", tls_cert=None, tls_key=None),
            auth_card_base_url="http://127.0.0.1:8780")
        with TestClient(server.streamable_http_app()) as client:
            def call(token, name, arguments):
                return client.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer " + token["token"], "MCP-Protocol-Version": "2025-06-18"},
                    json={"jsonrpc": "2.0", "id": "csv", "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}}).json()
            denied = call(tokens[0], "taihua_analytics_report_export", {"result_id": source["result_id"]})
            self.assertTrue(denied["result"]["isError"])
            report = call(tokens[1], "taihua_analytics_report_export", {"result_id": source["result_id"]})
            self.assertEqual(report["result"]["structuredContent"]["status"], "succeeded", report)
            rid = report["result"]["structuredContent"]["result"]["report_id"]
            content = call(tokens[1], "taihua_analytics_report_download", {"report_id": rid})
            self.assertEqual(content["result"]["structuredContent"]["result"]["schemaVersion"], "agentbridge.protected_csv_delivery.v1")
            self.worker.rows = []
            changed = call(tokens[1], "taihua_analytics_report_download", {"report_id": rid})
            self.assertEqual(changed["result"]["structuredContent"]["error"]["code"], "RESULT_ACCESS_REVOKED")

    def test_report_lifetime_capped_by_source_and_reissue_rechecks(self):
        from contextlib import closing
        self.permit_export()
        source = self.summary()
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        with closing(self.runtime.results._connect()) as conn, conn:
            conn.execute("UPDATE analysis_results SET expires_at=? WHERE result_id=?", (expiry.isoformat(), source["result_id"]))
        first = self.invoke(EXPORT, {"result_id": source["result_id"]}, idempotency_key="export")
        replay = self.invoke(EXPORT, {"result_id": source["result_id"]}, idempotency_key="export")
        self.assertEqual(first["result"], replay["result"])
        self.assertEqual(first["result"]["expires_at"], expiry.isoformat())
        self.worker.rows = []
        reissue = self.invoke(EXPORT, {"result_id": source["result_id"]}, idempotency_key="reissue")
        self.assertEqual(reissue["error"]["code"], "RESULT_ACCESS_REVOKED")

    def test_workspace_download_uses_owner_and_live_export_authority(self):
        from bscli.workspace.application import WorkspaceApplication, WorkspaceArtifactError
        self.permit_export()
        source = self.summary()
        task_id = self.task()
        report = self.invoke(EXPORT, {"result_id": source["result_id"]}, task_id=task_id)
        self.assertEqual(report["status"], "succeeded", report)
        rid = report["result"]["report_id"]
        artifacts = self.service.tasks.list_artifacts(task_id=task_id, user_subject="self")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["download_url"], f"/api/analytics/reports/{rid}/download")
        app = WorkspaceApplication(service=self.service)
        data = app.analytics_report({"user_subject": "self"}, rid)
        self.assertIn("登记工时", data["body"].decode("utf-8-sig"))
        with self.assertRaises(WorkspaceArtifactError):
            app.analytics_report({"user_subject": "other"}, rid)
        self.allowed = False
        with self.assertRaises(WorkspaceArtifactError):
            app.analytics_report({"user_subject": "self"}, rid)
