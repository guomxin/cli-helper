from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import zlib

from bscli.adapters.taihua import TaihuaCentralAdapter
from bscli.analytics.contracts import Budget, Query, SCOPES, SUMMARY, RESULT_GET, bigint, metrics
from bscli.analytics.results import AnalysisResultStore
from bscli.analytics.visibility import PersonalVisibilityProvider
from bscli.browser.http import CentralHttpWorker
from bscli.core.capability_runtime import CapabilityRejected
from bscli.core.central_service import CentralCapabilityService
from bscli.core.data_source_secrets import DataSourceSecretStore
from bscli.core.data_sources import DataSourceConfig
from bscli.core.session_secrets import AesGcmSessionStateProtector, SessionStateStore
from bscli.database.postgres_read import PostgresReadExecutor, ROWS_SQL, AGGREGATE_SQL, ROLE_SQL, CONTRACT_SQL, REQUIRED_COLUMNS, interruptible_query

ARGS = {"start_date": "2026-08-31", "end_date_exclusive": "2026-09-07", "log_type": "DAILY", "group_by": "day"}
ROW = {"id": 10, "userId": 7, "logDate": "2026-09-01", "hours": 1.5, "typeCode": "DAILY",
       "projectId": None, "createdAt": "2026-09-01T10:00:00", "content": "PRIVATE-BODY"}
PROTECTOR = AesGcmSessionStateProtector(b"a" * 32)


class Worker:
    def __init__(self, rows=None):
        self.rows = deepcopy([ROW] if rows is None else rows)
        self.total = None
        self.principal = {"id": 7, "username": "self", "fullname": "测试本人", "dept": {"id": 8}}
        self.calls = []
        self.state = {"authorization": "Bearer synthetic"}
        self.denied = False
        self.change_after = None
        self.personal_reads = 0

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def restore_session_state(self, state): self.state = state["http"]
    def capture_session_state(self): return {"cookies": [], "http": self.state}
    def get_http_state(self): return self.state.copy()
    def set_http_state(self, value): self.state = value
    def request(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        path = urlparse(url).path
        if path.endswith("/principal"):
            payload = self.principal
        elif path.endswith("/member-options"):
            payload = [{"id": 7, "username": "self", "fullname": "测试本人", "deptId": 8}]
        elif path.endswith("/range"):
            self.personal_reads += 1
            if self.change_after and self.personal_reads > 1:
                self.change_after(self)
            payload = deepcopy(self.rows)
        elif path.endswith("/team"):
            if self.denied:
                return {"status": 403, "json": {}, "text": "SECRET-ERROR"}
            params = parse_qs(urlparse(url).query)
            assert params["userId"] == ["7"] and params["deptId"] == ["8"]
            offset = (int(params["page"][0])-1)*100
            payload = {"content": deepcopy(self.rows[offset:offset+100]),
                       "totalElements": len(self.rows) if self.total is None else self.total}
        else:
            raise AssertionError(path)
        return {"status": 200, "json": payload, "text": ""}


class Cursor:
    def __init__(self, value): self.value = value
    def fetchall(self): return self.value
    def fetchone(self): return self.value


class Connection:
    def __init__(self):
        self.calls, self.closed, self.rolled_back = [], False, False
        self.unsafe, self.bad_schema, self.bad_owner = False, False, False
        self.running = threading.Event()
        self.canceled = threading.Event()
        self.block = False
        self.role_flags = {}

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql == ROLE_SQL:
            return Cursor({"readonly": True, "repeatable": True, "default_readonly": True,
                           "primary_key": True, "analysis_access": True, "temp": self.unsafe, **self.role_flags})
        if sql == CONTRACT_SQL:
            return Cursor([{"attname": n, "type": "text" if self.bad_schema else t, "readable": True}
                           for n, t in REQUIRED_COLUMNS.items()])
        if sql == ROWS_SQL:
            if self.block:
                self.running.set()
                self.canceled.wait(3)
                raise RuntimeError("secret SQL text")
            if not params[1]: return Cursor([])
            return Cursor([{"id": 10, "user_id": 9 if self.bad_owner else 7, "log_date": date(2026,9,1),
                            "hours": Decimal("1.5"), "type_code": "DAILY", "project_id": None,
                            "created_at": datetime(2026,9,1,10,0,0,123000), "updated_at": datetime(2026,9,1,10)}])
        if sql == AGGREGATE_SQL:
            m = metrics([] if not params[1] else [{"hours":"1.5","project_id":None,"log_date":"2026-09-01"}])
            return Cursor({k: m[k] for k in ("log_count", "registered_hours", "logged_people", "logged_person_days",
                                           "unassigned_project_log_count", "unassigned_project_hours")})
        return Cursor(None)
    def cancel_safe(self, **_kwargs): self.canceled.set()
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True


class Assertions(unittest.TestCase):
    def error(self, code, function):
        with self.assertRaises(CapabilityRejected) as raised: function()
        self.assertEqual(raised.exception.code, code)

    def provider(self, worker):
        return PersonalVisibilityProvider(TaihuaCentralAdapter(base_url="http://10.10.50.101"), worker, expected_principal="测试本人")


class ContractTests(Assertions):

    def test_input_rejects_dates_extra_fields_and_types(self):
        for patch_args in ({"start_date":"2026-02-30"}, {"start_date":"20260831"},
                           {"end_date_exclusive":"2026-09-08"}, {"group_by":"project"}, {"user_id":7}, {"sql":"SELECT 1"}):
            with self.subTest(patch_args=patch_args):
                self.error("INVALID_ANALYSIS_INPUT", lambda: Query.parse({**ARGS, **patch_args}))
        self.error("LOG_TYPE_UNSUPPORTED", lambda: Query.parse({**ARGS,"log_type":"WEEKLY"}))
        self.error("INVALID_ANALYSIS_INPUT", lambda: Query.parse(ARGS, today=date(2026,8,30)))

    def test_bigints_never_round_trip_through_float(self):
        self.assertEqual(bigint("9223372036854775807"), "9223372036854775807")
        for value in (True, 7.0, "1 OR 1=1", "9223372036854775808", -1):
            self.error("DATA_CONTRACT_CHANGED", lambda: bigint(value))

    def test_decimal_empty_zero_and_nonpositive_hours(self):
        self.assertIsNone(metrics([])["hours_per_logged_person_day"])
        self.assertEqual(metrics([])["logged_people"], 0)
        rows = [{"hours":h,"log_date":d,"project_id":None} for h,d in (("0.5","a"),("1","b"),("2","c"))]
        self.assertEqual(metrics(rows)["hours_per_logged_person_day"], "1.17")
        self.assertEqual(metrics(rows)["unassigned_project_hours_ratio"], "1.0000")
        self.assertIsNone(metrics([{**rows[0], "hours":"0"}])["unassigned_project_hours_ratio"])
        self.assertEqual(metrics([{**rows[0], "hours":"-0.5"}])["registered_hours"], "-0.5")

    def provider(self, worker):
        return PersonalVisibilityProvider(TaihuaCentralAdapter(base_url="http://10.10.50.101"), worker, expected_principal="测试本人")

    def test_visibility_self_filters_projected_and_complete(self):
        worker = Worker()
        result = self.provider(worker).capture(Query.parse(ARGS), Budget())
        self.assertEqual(result["principal_id"], "7")
        self.assertNotIn("PRIVATE-BODY", json.dumps(result))
        self.assertTrue(all(c[1]["max_response_bytes"] == 8*1024*1024 for c in worker.calls))

    def test_empty_requires_explicit_zero(self):
        worker = Worker([])
        self.assertEqual(self.provider(worker).capture(Query.parse(ARGS), Budget())["rows"], [])
        worker.total = "0"
        self.error("COVERAGE_UNVERIFIABLE", lambda: self.provider(worker).capture(Query.parse(ARGS), Budget()))

    def test_username_only_rows_require_exact_trusted_mapping(self):
        row = {k:v for k,v in ROW.items() if k != "userId"}
        worker = Worker([{**row, "username":"self"}])
        result = self.provider(worker).capture(Query.parse(ARGS), Budget())
        self.assertEqual(result["principal_id"], "7")
        self.assertEqual(result["principal_username"], "self")
        for bad in ({**row,"username":"other"}, row,
                    {**row,"username":"self","userId":99},
                    {**ROW,"username":"other"},
                    {**row,"username":"self","user":{"username":"other"}}):
            self.error("COVERAGE_UNVERIFIABLE", lambda: self.provider(Worker([bad])).capture(Query.parse(ARGS),Budget()))
        worker.principal["username"] = "other"
        self.error("IDENTITY_UNVERIFIED", lambda: self.provider(worker).capture(Query.parse(ARGS),Budget()))

    def test_denied_team_does_not_trigger_login(self):
        worker = Worker(); worker.denied = True
        self.error("COVERAGE_UNVERIFIABLE", lambda: self.provider(worker).capture(Query.parse(ARGS), Budget()))

    def test_duplicate_limit_owner_and_total_fail_closed(self):
        for worker, code in ((Worker([ROW,ROW]),"RESULT_INCOMPLETE"), (Worker([ROW]*500),"RESULT_INCOMPLETE"),
                             (Worker([{**ROW,"userId":99}]),"COVERAGE_UNVERIFIABLE")):
            self.error(code, lambda: self.provider(worker).capture(Query.parse(ARGS), Budget()))
        worker = Worker(); worker.total = 2
        self.error("RESULT_INCOMPLETE", lambda: self.provider(worker).capture(Query.parse(ARGS), Budget()))

    def test_identity_change_blocks_before_business_read(self):
        worker = Worker(); worker.principal["fullname"] = "other"
        self.error("IDENTITY_CHANGED", lambda: self.provider(worker).capture(Query.parse(ARGS), Budget()))
        self.assertEqual(len(worker.calls), 1)

    def test_real_pagination_contract(self):
        worker = Worker([{**ROW,"id":i+1} for i in range(201)])
        self.assertEqual(len(self.provider(worker).capture(Query.parse(ARGS), Budget())["rows"]),201)

    def test_budget_interrupt_timeout_and_request_limit(self):
        budget = Budget(); budget.canceled.set()
        self.error("INTERRUPTED", budget.check)
        budget = Budget(); budget.deadline = 0
        self.error("BUDGET_EXCEEDED", budget.check)
        budget = Budget(); budget.requests = 18
        self.error("BUDGET_EXCEEDED", budget.request)


class CentralTests(Assertions):
    def test_team_filter_failure_finishes_task_without_requesting_user_action(self):
        from unittest.mock import patch
        from bscli.adapters.taihua import TaihuaQueryFilterUnverified
        task_id = self.task()
        with patch("bscli.adapters.taihua.TaihuaCentralAdapter.invoke_capability",
                   side_effect=TaihuaQueryFilterUnverified("团队日志接口未按日志日期筛选，查询已停止。")):
            response = self.invoke("taihua.work_log.team.list", {"log_date": "2026-09-01"}, task_id=task_id)
        self.assertEqual(response["status"], "failed", response)
        self.assertEqual(response["error"]["code"], "QUERY_FILTER_UNVERIFIED")
        self.assertFalse(response.get("nextAction"))
        task = self.service.tasks.get_task(task_id, user_subject="self")
        self.assertEqual(task["status"], "failed")

    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name)
        states = SessionStateStore(self.root/"sessions", protector=PROTECTOR)
        self.service = CentralCapabilityService(home=self.root, base_url="https://oa.example",
            taihua_base_url="http://10.10.50.101", session_state_store=states)
        self.allowed = True
        self.service.set_task_plan_authority_resolver(lambda token, scopes: {"user_subject":"self"} if self.allowed and token == "token" and scopes == SCOPES else {})
        self.runtime = self.service.analytics_runtime()
        self.config = DataSourceConfig(enabled=True, username="readonly", privileges_reviewed=True, admitted_subjects=("self",))
        self.runtime.config = lambda: self.config
        self.runtime.secrets.save("taihua_primary:1", {"password":"SYNTHETIC-PASSWORD"})
        self.connection = Connection()
        self.runtime.executor = PostgresReadExecutor(self.runtime.secrets, connect=lambda **_kw: self.connection)
        self.worker = Worker()
        self.service._worker_factories_by_system["taihua"] = lambda *_args: self.worker
        session = self.service.sessions.get_or_create(user_subject="self",system_id="taihua",expected_principal_ref="测试本人")
        self.session = self.service.sessions.activate(session["session_id"], observed_principal_ref="测试本人")
        states.save(self.session["session_id"], self.worker.capture_session_state())

    def tearDown(self):
        if self.runtime.instance_lock: self.runtime.instance_lock.close()
        self.temp.cleanup()

    def invoke(self, capability=SUMMARY, arguments=None, **kwargs):
        return self.service.invoke(user_subject="self",capability_name=capability,
            arguments=ARGS if arguments is None else arguments, analytics_authority_id="token", **kwargs)



















    def test_idle_driver_poll_observes_cancel_and_closes_generator(self):
        closed = []
        def idle_driver():
            try:
                while True:
                    yield 1
            finally:
                closed.append(True)
        budget = Budget()
        guarded = interruptible_query(idle_driver(), budget)
        self.assertEqual(next(guarded),1)
        self.assertEqual(guarded.send(0),1)
        budget.canceled.set()
        self.error("INTERRUPTED", lambda:guarded.send(0))
        self.assertEqual(closed,[True])

    def task(self):
        return self.service.ensure_host_task(user_subject="self", token_id="token", agent_host="openclaw",
            host_task_key="analytics", endpoint_key="origin", client_type="web", external_subject="self",
            conversation_ref="private:self", title="本人日报分析", capabilities=["direct_status", "timeline_message"])["task"]["taskId"]










class HttpBoundTests(Assertions):
    def response(self, body, encoding="identity"):
        stream = io.BytesIO(body)
        stream.status = 200
        stream.headers = {"Content-Type":"application/json", "Content-Encoding":encoding}
        stream.geturl = lambda:"http://example.test/api"
        return stream

    def test_stream_and_compression_limits(self):
        worker = CentralHttpWorker(allowed_origins={"http://example.test"})
        for body,encoding in ((b"x"*101,"identity"),(zlib.compress(b"x"*1000),"deflate")):
            stream = self.response(body,encoding)
            with patch.object(worker,"_open",return_value=stream):
                self.error("RESULT_INCOMPLETE", lambda:worker.request("GET","http://example.test/api",max_response_bytes=100))
            self.assertTrue(stream.closed)

    def test_bounded_json_and_cancel(self):
        worker = CentralHttpWorker(allowed_origins={"http://example.test"})
        with patch.object(worker,"_open",return_value=self.response(b'{"ok":true}')):
            self.assertEqual(worker.request("GET","http://example.test/api",max_response_bytes=100)["json"],{"ok":True})
        canceled = threading.Event(); canceled.set()
        with patch.object(worker,"_open",return_value=self.response(b'{}')):
            self.error("INTERRUPTED", lambda:worker.request("GET","http://example.test/api",max_response_bytes=100,cancellation=canceled))


if __name__ == "__main__": unittest.main()
