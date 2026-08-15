from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlparse
import unittest

from bscli.adapters.smartlight import (
    SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY,
    SMARTLIGHT_ALARM_LIST_CAPABILITY,
    SMARTLIGHT_ALARM_REMARK_UPDATE_CAPABILITY,
    SMARTLIGHT_ALARM_REMARK_UPDATE_PREPARE_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_REVOKE_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_REVOKE_PREPARE_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_CAPABILITY,
    SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_PREPARE_CAPABILITY,
    SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
    SMARTLIGHT_ASSET_SEARCH_CAPABILITY,
    SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY,
    SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
    SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
    SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY,
    SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
    SMARTLIGHT_OVERVIEW_CAPABILITY,
    SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
    SMARTLIGHT_RTU_ALARM_DISPOSE_CAPABILITY,
    SMARTLIGHT_RTU_ALARM_DISPOSE_PREPARE_CAPABILITY,
    SmartlightAlarmActionOutcomeUnknown,
    SmartlightBusinessRuleRejected,
    SmartlightAuthenticationRejected,
    SmartlightCentralAdapter,
    SmartlightLoginRequired,
    _resolve_date_range,
    build_smartlight_capability_registry,
    commit_smartlight_alarm_remark_update,
    commit_smartlight_alarm_work_area_revoke,
    commit_smartlight_alarm_work_area_submit,
    commit_smartlight_rtu_alarm_dispose,
    prepare_smartlight_alarm_work_area_revoke,
    prepare_smartlight_alarm_work_area_submit,
    prepare_smartlight_alarm_remark_update,
    prepare_smartlight_rtu_alarm_dispose,
)
from bscli.core.central_service import capability_required_scopes


class SmartlightAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = SmartlightCentralAdapter(
            base_url="http://123.232.113.241:4101/smartlight",
            allow_insecure_http=True,
        )

    def test_plain_http_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            SmartlightCentralAdapter(
                base_url="http://123.232.113.241:4101/smartlight"
            )

    def test_authentication_contract_uses_trusted_captcha_card(self):
        contract = self.adapter.authentication_contract()

        self.assertEqual(contract["system_id"], "smartlight")
        self.assertNotIn("authentication_mode", contract)
        self.assertEqual(
            [field["name"] for field in contract["fields"]],
            ["username", "password", "authcode"],
        )
        self.assertEqual(
            contract["prepared_authentication"]["kind"],
            "image_captcha",
        )

    def test_prepares_captcha_then_authenticates_and_captures_jwt(self):
        worker = FakeSmartlightWorker()

        prepared = self.adapter.prepare_authentication(worker, timeout_seconds=20)
        recovered = self.adapter.recover_prepared_authentication(worker)
        result = self.adapter.authenticate(
            worker,
            {"username": "yanshi", "password": "secret", "authcode": "1234"},
            timeout_seconds=20,
        )

        self.assertEqual(prepared["captcha"]["content_type"], "image/jpeg")
        self.assertEqual(recovered, prepared)
        self.assertEqual(result["observed_principal_ref"], "无为")
        self.assertEqual(result["principal"]["account"], "yanshi")
        self.assertNotIn("password_digest", result["principal"])
        self.assertEqual(worker.state["access_token"], "jwt-access")
        submitted = parse_qs(worker.cas_submission)
        self.assertEqual(submitted["username"], ["eWFuc2hp"])
        self.assertEqual(submitted["authcode"], ["1234"])
        self.assertEqual(
            submitted["password"],
            ["5EBE2294ECD0E0F08EAB7690D2A6EE69"],
        )
        self.assertEqual(worker.cas_headers["Origin"], self.adapter.origin)
        self.assertIn("/cas/login", worker.cas_headers["Referer"])
        self.assertIn("Mozilla/5.0", worker.cas_headers["User-Agent"])
        self.assertIn("/cas/login", worker.captcha_headers["Referer"])

    def test_probe_session_prefers_refresh_token_without_requiring_cas(self):
        worker = FakeSmartlightWorker(authenticated=True)

        result = self.adapter.probe_session(worker)

        self.assertTrue(result["authenticated"])
        self.assertEqual(result["observed_principal_ref"], "无为")
        self.assertEqual(worker.state["access_token"], "jwt-access-refreshed")
        self.assertEqual(worker.refresh_requests, 1)
        self.assertEqual(worker.principal_requests, 0)
        self.assertEqual(worker.token_exchange_requests, 0)

    def test_probe_session_falls_back_to_cas_when_refresh_is_rejected(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.reject_refresh_token = True

        result = self.adapter.probe_session(worker)

        self.assertTrue(result["authenticated"])
        self.assertEqual(worker.state["access_token"], "jwt-access")
        self.assertEqual(worker.refresh_requests, 1)
        self.assertEqual(worker.principal_requests, 1)
        self.assertEqual(worker.token_exchange_requests, 1)

    def test_probe_session_classifies_empty_principal_as_logged_out(self):
        worker = FakeSmartlightWorker()
        worker.empty_principal = True

        with self.assertRaises(SmartlightLoginRequired):
            self.adapter.probe_session(worker)

        self.assertEqual(worker.principal_requests, 1)
        self.assertEqual(worker.token_exchange_requests, 0)

    def test_classifies_captcha_rejection_from_cas_error_page(self):
        worker = FakeSmartlightWorker(login_rejection="验证码错误，请重新输入")
        self.adapter.prepare_authentication(worker, timeout_seconds=20)

        with self.assertRaises(SmartlightAuthenticationRejected) as raised:
            self.adapter.authenticate(
                worker,
                {"username": "yanshi", "password": "secret", "authcode": "1234"},
                timeout_seconds=20,
            )

        self.assertEqual(raised.exception.error_code, "CAPTCHA_REJECTED")

    def test_classifies_account_rejection_from_cas_error_page(self):
        worker = FakeSmartlightWorker(login_rejection="用户名或密码错误")
        self.adapter.prepare_authentication(worker, timeout_seconds=20)

        with self.assertRaises(SmartlightAuthenticationRejected) as raised:
            self.adapter.authenticate(
                worker,
                {"username": "yanshi", "password": "secret", "authcode": "1234"},
                timeout_seconds=20,
            )

        self.assertEqual(raised.exception.error_code, "CREDENTIALS_REJECTED")

    def test_read_capabilities_use_observed_api_contracts(self):
        worker = FakeSmartlightWorker(authenticated=True)

        overview = self.adapter.invoke_capability(
            SMARTLIGHT_OVERVIEW_CAPABILITY, worker, {}
        )
        lamp_posts = self.adapter.invoke_capability(
            SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
            worker,
            {"keyword": "LP-", "page": 1, "size": 10},
        )
        alarms = self.adapter.invoke_capability(
            SMARTLIGHT_ALARM_LIST_CAPABILITY,
            worker,
            {"page": 1, "size": 10},
        )
        tasks = self.adapter.invoke_capability(
            SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            worker,
            {"task_name": "夜巡", "state": 2, "page": 1, "size": 10},
        )
        leakage = self.adapter.invoke_capability(
            SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
            worker,
            {"start_date": "2026-08-01", "end_date": "2026-08-11"},
        )

        self.assertEqual(overview["cabinetTotal"], 1)
        self.assertEqual(overview["lampPostTotal"], 131)
        self.assertEqual(
            overview["lampPostCounts"],
            {"searchable": 131, "mapDetail": 116},
        )
        self.assertEqual(lamp_posts["items"][0]["code"], "LP-001")
        self.assertEqual(alarms["summary"]["untreated"], 2)
        self.assertEqual(alarms["sort"]["field"], "lastActivityAt")
        self.assertEqual(alarms["summaryScope"]["type"], "current_system_snapshot")
        self.assertEqual(alarms["items"][0]["id"], "alarm-1")
        self.assertEqual(alarms["items"][0]["occurredAt"], "2026-08-10 10:05:38")
        self.assertEqual(
            alarms["items"][0]["lastActivityAt"],
            "2026-08-12 10:30:02",
        )
        self.assertEqual(alarms["items"][0]["type"], "电源缺相")
        self.assertEqual(alarms["items"][0]["message"], "电源缺相(B,C)")
        self.assertEqual(alarms["items"][0]["alarmWeight"], 3)
        self.assertEqual(alarms["items"][0]["workAreaId"], "work-area-1")
        self.assertEqual(alarms["items"][0]["workArea"], "实验室工区")
        self.assertFalse(alarms["items"][0]["workAreaSubmitted"])
        self.assertEqual(alarms["items"][0]["workAreaSubmitState"], 0)
        self.assertEqual(
            alarms["items"][0]["workAreaSubmitStateLabel"],
            "未提交",
        )
        self.assertEqual(tasks["items"][0]["taskName"], "夜巡一组")
        self.assertEqual(tasks["items"][0]["inspectionGroup"], "一号巡检组")
        self.assertEqual(tasks["items"][0]["startTime"], "2026-08-01")
        self.assertEqual(tasks["items"][0]["endTime"], "2026-08-31")
        self.assertEqual(tasks["items"][0]["progress"], "25.00%")
        self.assertEqual(tasks["items"][0]["stateCode"], 2)
        self.assertEqual(tasks["items"][0]["stateLabel"], "执行中")
        self.assertEqual(
            tasks["items"][0]["deviceCounts"],
            {"confirmed": 4, "lampPosts": 12, "rtus": 2},
        )
        self.assertEqual(
            tasks["items"][0]["progressScope"],
            "downstream_reported_independent_metric",
        )
        self.assertEqual(leakage["summary"]["untreated"], 1)
        self.assertEqual(leakage["rangeSummary"], {"recordTotal": 1})
        self.assertFalse(leakage["summaryScope"]["dateRangeApplied"])
        self.assertEqual(leakage["dateRange"]["source"], "explicit")
        self.assertEqual(leakage["items"][0]["value"], 12)
        self.assertTrue(
            all("x-Authentication-Token" in headers for headers in worker.api_headers)
        )
        count_request = next(
            item
            for item in worker.api_requests
            if item["path"].endswith("/lHisHitchAlarm/getCountDataByCondition")
        )
        count_query = json.loads(parse_qs(count_request["body"])["json"][0])
        self.assertEqual(count_query["_timebegin_lastDate"], "")
        self.assertEqual(count_query["_timeend_lastDate"], "")
        task_request = next(
            item
            for item in worker.api_requests
            if item["path"].endswith("/inspectionTask/getDataByCondition")
        )
        task_query = json.loads(parse_qs(task_request["body"])["json"][0])
        self.assertEqual(task_query["_taskState"], 2)

    def test_alarm_list_normalizes_blank_work_area_state_as_not_submitted(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.alarm_record["isSubmitWorkArea"] = ""

        alarms = self.adapter.invoke_capability(
            SMARTLIGHT_ALARM_LIST_CAPABILITY,
            worker,
            {"page": 1, "size": 10},
        )

        self.assertFalse(alarms["items"][0]["workAreaSubmitted"])
        self.assertEqual(alarms["items"][0]["workAreaSubmitState"], 0)

    def test_relative_date_range_is_computed_in_business_timezone(self):
        start, end, source, last_days = _resolve_date_range(
            {"last_days": 30},
            now=datetime(
                2026,
                8,
                12,
                1,
                30,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )

        self.assertEqual(start, "2026-07-14")
        self.assertEqual(end, "2026-08-12")
        self.assertEqual(source, "last_days")
        self.assertEqual(last_days, 30)

    def test_inspection_state_string_is_normalized_before_downstream_request(self):
        worker = FakeSmartlightWorker(authenticated=True)

        result = self.adapter.invoke_capability(
            SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            worker,
            {"state": "2", "page": 1, "size": 3},
        )

        task_request = next(
            item
            for item in worker.api_requests
            if item["path"].endswith("/inspectionTask/getDataByCondition")
        )
        task_query = json.loads(parse_qs(task_request["body"])["json"][0])
        self.assertEqual(task_query["_taskState"], 2)
        self.assertEqual(result["filters"]["state"], 2)

    def test_phase_two_asset_detail_and_bounded_analysis_contracts(self):
        worker = FakeSmartlightWorker(authenticated=True)

        cabinets = self.adapter.invoke_capability(
            SMARTLIGHT_ASSET_SEARCH_CAPABILITY,
            worker,
            {"asset_type": "cabinet", "keyword": "一号", "page": 1, "size": 20},
        )
        rtu = self.adapter.invoke_capability(
            SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
            worker,
            {"asset_type": "rtu", "asset_id": "rtu-1"},
        )
        lamp_post = self.adapter.invoke_capability(
            SMARTLIGHT_ASSET_DETAIL_CAPABILITY,
            worker,
            {"asset_type": "lamppost", "asset_id": "lp-1"},
        )
        alarms = self.adapter.invoke_capability(
            SMARTLIGHT_ALARM_ANALYSIS_CAPABILITY,
            worker,
            {"alarm_state": "current", "top_n": 5},
        )
        inspection = self.adapter.invoke_capability(
            SMARTLIGHT_INSPECTION_TASK_DETAIL_CAPABILITY,
            worker,
            {"task_id": "task-1", "detail_date": "2026-08-12"},
        )
        leakage = self.adapter.invoke_capability(
            SMARTLIGHT_LEAKAGE_ANALYSIS_CAPABILITY,
            worker,
            {"last_days": 30, "top_n": 5},
        )

        self.assertEqual(cabinets["items"][0]["code"], "CAB-001")
        self.assertTrue(rtu["found"])
        self.assertEqual(rtu["detail"]["code"], "RTU-001")
        self.assertEqual(rtu["relayTotal"], 1)
        self.assertEqual(rtu["relays"][0]["circuitCount"], 3)
        self.assertTrue(lamp_post["found"])
        self.assertEqual(lamp_post["detail"]["height"], 12)
        self.assertEqual(alarms["dateRange"]["source"], "default_last_days")
        self.assertEqual(alarms["analyzedCount"], 1)
        self.assertFalse(alarms["truncated"])
        self.assertEqual(
            alarms["stateCounts"],
            [{"value": "当前告警", "count": 1}],
        )
        self.assertEqual(inspection["dailyCount"], 1)
        self.assertEqual(inspection["days"][0]["plannedDeviceCount"], 10)
        self.assertTrue(inspection["detailDateFound"])
        self.assertEqual(inspection["clockins"][0]["deviceCode"], "LP-001")
        self.assertEqual(leakage["analyzedCount"], 1)
        self.assertEqual(
            leakage["topLampPosts"],
            [{"value": "LP-001", "count": 1}],
        )
        alarm_request = next(
            item
            for item in worker.api_requests
            if item["path"].endswith("/rHisHitchAlarm/getDataByRtuAlarm")
            and "_timebegin_begin" in item["body"]
        )
        alarm_query = json.loads(parse_qs(alarm_request["body"])["json"][0])
        self.assertEqual(alarm_query["dateType"], "1")
        self.assertEqual(alarm_query["_include_conductStatue"], ["131"])

    def test_relative_and_explicit_date_ranges_cannot_be_mixed(self):
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            _resolve_date_range(
                {"last_days": 30, "start_date": "2026-08-01"}
            )

    def test_report_export_returns_bounded_rows_and_csv_contract_metadata(self):
        worker = FakeSmartlightWorker(authenticated=True)

        alarm_report = self.adapter.invoke_capability(
            SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
            worker,
            {"report_type": "alarm_analysis", "last_days": 30},
        )
        asset_report = self.adapter.invoke_capability(
            SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
            worker,
            {"report_type": "asset_inventory", "asset_type": "rtu"},
        )
        inspection_report = self.adapter.invoke_capability(
            SMARTLIGHT_REPORT_EXPORT_CAPABILITY,
            worker,
            {
                "report_type": "inspection_progress",
                "task_id": "task-1",
                "detail_date": "2026-08-12",
            },
        )

        self.assertEqual(alarm_report["reportType"], "alarm_analysis")
        self.assertEqual(alarm_report["metadata"]["exportedCount"], 1)
        self.assertEqual(alarm_report["rows"][0]["id"], "alarm-1")
        self.assertEqual(asset_report["reportTitle"], "照明RTU清单")
        self.assertEqual(asset_report["rows"][0]["code"], "RTU-001")
        self.assertEqual(inspection_report["metadata"]["exportedCount"], 1)
        self.assertEqual(inspection_report["rows"][0]["deviceCode"], "LP-001")

    def test_registry_contains_read_and_reversible_write_capabilities(self):
        capabilities = build_smartlight_capability_registry().list()

        self.assertEqual(len(capabilities), 19)
        self.assertEqual(
            sum(spec.effect == "read" for spec in capabilities),
            11,
        )
        self.assertEqual(
            {
                spec.name
                for spec in capabilities
                if spec.effect == "reversible_write"
            },
            {
                SMARTLIGHT_ALARM_REMARK_UPDATE_PREPARE_CAPABILITY,
                SMARTLIGHT_ALARM_REMARK_UPDATE_CAPABILITY,
                SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_PREPARE_CAPABILITY,
                SMARTLIGHT_ALARM_WORK_AREA_SUBMIT_CAPABILITY,
                SMARTLIGHT_ALARM_WORK_AREA_REVOKE_PREPARE_CAPABILITY,
                SMARTLIGHT_ALARM_WORK_AREA_REVOKE_CAPABILITY,
            },
        )
        self.assertEqual(
            {
                spec.name
                for spec in capabilities
                if spec.effect == "controlled_write"
            },
            {
                SMARTLIGHT_RTU_ALARM_DISPOSE_PREPARE_CAPABILITY,
                SMARTLIGHT_RTU_ALARM_DISPOSE_CAPABILITY,
            },
        )
        inspection = next(
            spec
            for spec in capabilities
            if spec.name == SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY
        )
        self.assertEqual(inspection.version, "0.2.1")
        self.assertEqual(
            inspection.input_schema["properties"]["state"]["type"],
            ["integer", "string", "null"],
        )

    def test_alarm_remark_update_freezes_commits_verifies_and_exposes_rollback(self):
        worker = FakeSmartlightWorker(authenticated=True)
        entered = []

        prepared = prepare_smartlight_alarm_remark_update(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1", "remark": "现场已复核"},
        )
        self.assertEqual(prepared["summary"]["system"], "照明实验室测试系统")
        result = commit_smartlight_alarm_remark_update(
            self.adapter,
            worker,
            prepared["plan"],
            enter_commit_boundary=lambda: entered.append(True),
        )

        self.assertEqual(entered, [True])
        self.assertEqual(worker.alarm_remark["remark"], "现场已复核")
        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["verification"]["matched"])
        self.assertEqual(
            result["rollback"]["arguments"],
            {"alarm_id": "alarm-1", "remark": ""},
        )
        prepared_clear = prepare_smartlight_alarm_remark_update(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1", "remark": ""},
        )
        cleared = commit_smartlight_alarm_remark_update(
            self.adapter,
            worker,
            prepared_clear["plan"],
            enter_commit_boundary=lambda: entered.append("clear"),
        )
        self.assertEqual(cleared["alarm"]["remark"], "")
        self.assertIn("已清除", cleared["effect"])
        self.assertTrue(cleared["verification"]["matched"])
        self.assertEqual(
            capability_required_scopes(
                SMARTLIGHT_ALARM_REMARK_UPDATE_PREPARE_CAPABILITY
            ),
            frozenset({"smartlight:write:alarm_remark"}),
        )
        self.assertEqual(
            capability_required_scopes(SMARTLIGHT_ALARM_REMARK_UPDATE_CAPABILITY),
            frozenset({"smartlight:write:alarm_remark"}),
        )

    def test_alarm_remark_update_stops_when_remark_changed_after_prepare(self):
        worker = FakeSmartlightWorker(authenticated=True)
        prepared = prepare_smartlight_alarm_remark_update(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1", "remark": "拟写入备注"},
        )
        worker.alarm_remark = {
            "hitchAlarmId": "alarm-1",
            "rtuId": "rtu-1",
            "remark": "其他用户已修改",
        }

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "其他操作修改",
        ):
            commit_smartlight_alarm_remark_update(
                self.adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: self.fail(
                    "commit boundary must not be entered"
                ),
            )

    def test_work_area_submit_and_revoke_are_frozen_verified_action_pair(self):
        worker = FakeSmartlightWorker(authenticated=True)
        entered = []

        prepared_submit = prepare_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        submitted = commit_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            prepared_submit["plan"],
            enter_commit_boundary=lambda: entered.append("submit"),
        )
        prepared_revoke = prepare_smartlight_alarm_work_area_revoke(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        revoked = commit_smartlight_alarm_work_area_revoke(
            self.adapter,
            worker,
            prepared_revoke["plan"],
            enter_commit_boundary=lambda: entered.append("revoke"),
        )

        self.assertEqual(entered, ["submit", "revoke"])
        self.assertTrue(submitted["verification"]["matched"])
        self.assertEqual(
            submitted["rollback"]["capability"],
            SMARTLIGHT_ALARM_WORK_AREA_REVOKE_PREPARE_CAPABILITY,
        )
        self.assertFalse(revoked["alarm"]["workAreaSubmitted"])
        self.assertEqual(worker.alarm_record["isSubmitWorkArea"], 0)

        action_reads = [
            request
            for request in worker.api_requests
            if request["path"].endswith("/rHisHitchAlarm/getDataByRtuAlarm")
        ]
        self.assertGreaterEqual(len(action_reads), 6)
        for request in action_reads:
            query = parse_qs(request["body"])
            filters = json.loads(query["json"][0])
            self.assertEqual(filters["usageType"], 1)
            self.assertEqual(filters["_include_conductStatue"], ["131", "132"])
            self.assertEqual(filters["_include_isSubmitWorkArea"], [])
            self.assertEqual(filters["weightFacto"], [1, 2, 3, 4, 5, 6])
            self.assertTrue(filters["showData"])
            self.assertEqual(filters["userId"], "user-1")

    def test_work_area_prepare_rejects_alarm_outside_actionable_view(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.hide_alarm_from_actionable_view = True

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "actionable RTU alarm view",
        ):
            prepare_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                {"alarm_id": "alarm-1"},
            )

    def test_work_area_submit_rejects_non_work_area_alarm(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.alarm_record["weightFacto"] = 2

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "等级不是工区接收范围",
        ):
            prepare_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                {"alarm_id": "alarm-1"},
            )

    def test_work_area_submit_rejects_blank_work_area_name(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.alarm_record["workAreaName"] = ""

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "没有有效所属工区",
        ):
            prepare_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                {"alarm_id": "alarm-1"},
            )

    def test_work_area_revoke_allows_blank_work_area_name_for_recovery(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.alarm_record["isSubmitWorkArea"] = 1
        worker.alarm_record["workAreaName"] = ""

        prepared = prepare_smartlight_alarm_work_area_revoke(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )

        self.assertEqual(prepared["plan"]["business_intent"], "revoke_work_area")
        self.assertIsNone(prepared["plan"]["preconditions"]["workAreaName"])

    def test_work_area_submit_stops_when_snapshot_changes_after_prepare(self):
        worker = FakeSmartlightWorker(authenticated=True)
        prepared = prepare_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        worker.alarm_record["workAreaId"] = "work-area-2"

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "已经变化",
        ):
            commit_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: self.fail(
                    "commit boundary must not be entered"
                ),
            )

    def test_work_area_submit_rejects_unknown_submission_state(self):
        worker = FakeSmartlightWorker(authenticated=True)
        worker.alarm_record["isSubmitWorkArea"] = 9

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "工区提交状态无法识别",
        ):
            prepare_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                {"alarm_id": "alarm-1"},
            )

    def test_work_area_submit_stops_when_rtu_binding_changes_after_prepare(self):
        worker = FakeSmartlightWorker(authenticated=True)
        prepared = prepare_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        worker.alarm_record["rtuId"] = "rtu-2"
        worker.alarm_record["isSubmitWorkArea"] = 1

        with self.assertRaisesRegex(
            SmartlightBusinessRuleRejected,
            "RTU 已变化",
        ):
            commit_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: self.fail(
                    "commit boundary must not be entered"
                ),
            )

    def test_rtu_alarm_dispose_is_irreversible_and_verified(self):
        worker = FakeSmartlightWorker(authenticated=True)
        entered = []

        prepared = prepare_smartlight_rtu_alarm_dispose(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        result = commit_smartlight_rtu_alarm_dispose(
            self.adapter,
            worker,
            prepared["plan"],
            enter_commit_boundary=lambda: entered.append(True),
        )

        self.assertEqual(entered, [True])
        self.assertEqual(result["alarm"]["alarmState"], 3)
        self.assertFalse(result["rollback"]["available"])
        self.assertEqual(worker.alarm_record["conductStatue"], 3)
        self.assertEqual(
            capability_required_scopes(SMARTLIGHT_RTU_ALARM_DISPOSE_CAPABILITY),
            frozenset({"smartlight:write:alarm_disposition"}),
        )

    def test_alarm_action_timeout_after_commit_boundary_is_outcome_unknown(self):
        worker = FakeSmartlightWorker(authenticated=True)
        prepared = prepare_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        worker.alarm_action_request_error = ConnectionError("timed out")
        entered = []

        with self.assertRaisesRegex(
            SmartlightAlarmActionOutcomeUnknown,
            "最终结果无法确认",
        ):
            commit_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: entered.append(True),
            )
        self.assertEqual(entered, [True])

    def test_alarm_action_readback_failure_is_outcome_unknown(self):
        worker = FakeSmartlightWorker(authenticated=True)
        prepared = prepare_smartlight_alarm_work_area_submit(
            self.adapter,
            worker,
            {"alarm_id": "alarm-1"},
        )
        worker.fail_alarm_readback_after_action = True
        entered = []

        with self.assertRaisesRegex(
            SmartlightAlarmActionOutcomeUnknown,
            "权威回读失败",
        ):
            commit_smartlight_alarm_work_area_submit(
                self.adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: entered.append(True),
            )
        self.assertEqual(entered, [True])


class FakePage:
    def content(self) -> str:
        return """
        <form id="auth-form" action="/cas/login;jsessionid=test?service=x">
          <input type="hidden" name="lt" value="LT-test">
          <input type="hidden" name="execution" value="exec-test">
          <input type="hidden" name="_eventId" value="submit">
          <img id="captchaImage" src="captcha.jpg">
        </form>
        """


class FakeSmartlightWorker:
    def __init__(
        self,
        *,
        authenticated: bool = False,
        login_rejection: str | None = None,
    ) -> None:
        self.state = {}
        self.page_url = ""
        self.cas_submission = ""
        self.cas_headers: dict = {}
        self.captcha_headers: dict = {}
        self.api_headers: list[dict] = []
        self.api_requests: list[dict] = []
        self.login_rejection = login_rejection
        self.login_completed = authenticated
        self.reject_refresh_token = False
        self.empty_principal = False
        self.refresh_requests = 0
        self.principal_requests = 0
        self.token_exchange_requests = 0
        self.alarm_remark: dict | None = None
        self.saved_alarm_payloads: list[dict] = []
        self.alarm_action_request_error: Exception | None = None
        self.fail_alarm_readback_after_action = False
        self.alarm_action_performed = False
        self.hide_alarm_from_actionable_view = False
        self.alarm_record = {
            "hitchAlarmId": "alarm-1",
            "hitchName": "电源缺相",
            "hitchIntro": "电源缺相(B,C)",
            "occurDate": "2026-08-10 10:05:38",
            "lastDate": "2026-08-12 10:30:02",
            "rtuCode": "05312222",
            "rtuId": "rtu-1",
            "rtuName": "实验室控制柜",
            "weightFacto": 3,
            "conductStatue": 0,
            "workAreaId": "work-area-1",
            "workAreaName": "实验室工区",
            "isSubmitWorkArea": 0,
            "groupName": "RTU分组测试",
        }
        if authenticated:
            self.state = {
                "access_token": "jwt-access",
                "refresh_token": "jwt-refresh",
                "principal": _safe_principal(),
            }

    def goto(self, url: str, *, timeout_seconds: float = 30):
        del url, timeout_seconds
        self.page_url = "http://123.232.113.241:4101/cas/login?service=test"
        return FakePage()

    def request_bytes(self, method: str, url: str, **kwargs) -> dict:
        path = urlparse(url).path
        if method == "GET" and path == "/smartlight/" and not self.login_completed:
            return {
                "status": 302,
                "url": url,
                "content_type": "text/html",
                "body": b"",
                "location": "/cas/login?service=http%3A%2F%2Ftest%2Fsmartlight%2F",
            }
        if method == "GET" and path == "/cas/login":
            return {
                "status": 200,
                "url": url,
                "content_type": "text/html",
                "body": FakePage().content().encode("utf-8"),
                "location": None,
            }
        if method == "GET" and path.endswith("/cas/captcha.jpg"):
            self.captcha_headers = dict(kwargs.get("headers") or {})
            return {
                "status": 200,
                "url": url,
                "content_type": "image/jpeg",
                "body": b"jpeg-captcha",
                "location": None,
            }
        if method == "POST" and path.startswith("/cas/login"):
            self.cas_submission = kwargs["body"]
            self.cas_headers = dict(kwargs.get("headers") or {})
            if self.login_rejection is not None:
                body = f"""
                <form id="auth-form">
                  <div class="controls login-error-info">
                    {self.login_rejection}
                  </div>
                </form>
                """.encode("utf-8")
                return {
                    "status": 200,
                    "url": url,
                    "content_type": "text/html; charset=UTF-8",
                    "body": body,
                    "location": None,
                }
            self.login_completed = True
            return {
                "status": 302,
                "url": url,
                "content_type": "text/html",
                "body": b"",
                "location": "/smartlight/?ticket=ST-test",
            }
        if method == "GET" and path == "/smartlight/":
            return {
                "status": 200,
                "url": url,
                "content_type": "text/html",
                "body": b"app",
                "location": None,
            }
        raise AssertionError(f"unexpected bytes request: {method} {url}")

    def request(self, method: str, url: str, **kwargs) -> dict:
        self.assert_post(method)
        path = urlparse(url).path
        if path == "/smartlight/userInfo/getCasLoginUser":
            self.principal_requests += 1
            if self.empty_principal:
                return _response(
                    {
                        "dlzh": None,
                        "userName": None,
                        "dlmm": None,
                        "organroleId": None,
                        "yhid": None,
                    }
                )
            return _response(
                {
                    "dlzh": "yanshi",
                    "userName": "无为",
                    "dlmm": "D" * 96,
                    "organId": "org-1",
                    "organroleId": "role-1",
                    "organroleName": "wuwei",
                    "ryid": "person-1",
                    "yhid": "user-1",
                }
            )
        if path == "/jwtcenter//JWTInfoController/getToken":
            self.token_exchange_requests += 1
            return _response(
                {
                    "resp_code": 0,
                    "resp_data": {
                        "access_token": "jwt-access",
                        "refresh_token": "jwt-refresh",
                        "access_token_duration": 3600,
                    },
                }
            )
        if path == "/jwtcenter//JWTInfoController/refreshToken":
            self.refresh_requests += 1
            if self.reject_refresh_token:
                response = _response({"resp_code": 1001, "resp_data": None})
                response["status"] = 401
                return response
            return _response(
                {
                    "resp_code": 1000,
                    "resp_data": {
                        "access_token": "jwt-access-refreshed",
                        "access_token_duration": 1800,
                    },
                }
            )
        self.api_headers.append(dict(kwargs.get("headers") or {}))
        self.api_requests.append(
            {"path": path, "body": str(kwargs.get("body") or "")}
        )
        if path.endswith("/map/getIntegratedRControlCabinetDataByCondition"):
            return _response(
                [
                    {
                        "controlCabinetId": "cab-1",
                        "controlCabinetCode": "CAB-001",
                        "controlCabinetName": "一号箱变",
                        "onlineState": "online",
                    }
                ]
            )
        if path.endswith("/lLamppost/getLampDetialList"):
            return _response({"list": [], "totalCount": 116})
        if path.endswith("/lLamppost/getDataByConditionForFacilityEx"):
            return _response(
                {
                    "list": [
                        {
                            "lampPostID": "lp-1",
                            "LampPostCode": "LP-001",
                            "StreetName": "测试路",
                        }
                    ],
                    "totalCount": 131,
                }
            )
        if path.endswith("/lLamppost/getLampPostDetail"):
            return _response(
                {
                    "code": "200",
                    "result": {
                        "lampPostId": "lp-1",
                        "lampPostCode": "LP-001",
                        "lampPostTypeName": "十二米杆",
                        "lampPostHeight": 12,
                        "streetName": "测试路",
                        "controlCabinetName": "一号箱变",
                        "rtuName": "一号 RTU",
                        "workAreaName": "实验室工区",
                    },
                }
            )
        if path.endswith("/rControlCabinet/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "controlCabinetId": "cab-1",
                            "controlCabinetCode": "CAB-001",
                            "controlCabinetName": "一号箱变",
                            "capacityStr": "100kVA",
                            "workAreaName": "实验室工区",
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/rRtu/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "rtuId": "rtu-1",
                            "rtuCode": "RTU-001",
                            "rtuName": "一号 RTU",
                            "productModel": "RTU-X",
                            "controlCabinetName": "一号箱变",
                            "groupName": "测试组",
                            "runningStateName": "在线",
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/rRturelay/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "rturelayId": "relay-1",
                            "rturelayNumber": 1,
                            "rturelayName": "1",
                            "workModelName": "全夜灯",
                            "isEnabled": 1,
                            "roadSwitchList": [
                                {
                                    "roadSwitchId": "switch-1",
                                    "roadSwitchNumber": 1,
                                    "rRturoadList": [
                                        {"rturoadId": "road-a", "rturoadNumber": 1, "powerType": "A"},
                                        {"rturoadId": "road-b", "rturoadNumber": 2, "powerType": "B"},
                                        {"rturoadId": "road-c", "rturoadNumber": 3, "powerType": "C"},
                                    ],
                                }
                            ],
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/rHisHitchAlarm/getDataByRtuAlarm"):
            if self.fail_alarm_readback_after_action and self.alarm_action_performed:
                raise ConnectionError("readback failed")
            query = parse_qs(str(kwargs.get("body") or ""))
            filters_json = query.get("json", ["{}"])[0]
            filters = json.loads(filters_json)
            if (
                self.hide_alarm_from_actionable_view
                and filters.get("showData") is True
            ):
                return _response(
                    {
                        "RtuHisHitchAlarm": {"list": [], "totalCount": 0},
                        "todayAlarm": 0,
                        "untreated": 0,
                        "yesterdayAlarm": 0,
                    }
                )
            return _response(
                {
                    "RtuHisHitchAlarm": {
                    "list": [deepcopy(self.alarm_record)],
                        "totalCount": 1,
                    },
                    "todayAlarm": 3,
                    "untreated": 2,
                    "yesterdayAlarm": 1,
                }
            )
        if path.endswith("/rHisHitchAlarm/getRtuAlarmRemark"):
            return _response(deepcopy(self.alarm_remark))
        if path.endswith("/rHisHitchAlarm/saveRtuAlarmRemark"):
            payload = json.loads(parse_qs(str(kwargs.get("body") or ""))["json"][0])
            self.saved_alarm_payloads.append(deepcopy(payload))
            self.alarm_remark = deepcopy(payload)
            return _response({"code": 200, "message": "保存成功"})
        if path.endswith("/rHisHitchAlarm/updateIsSubmitWorkArea"):
            if self.alarm_action_request_error is not None:
                raise self.alarm_action_request_error
            self.alarm_record["isSubmitWorkArea"] = 1
            self.alarm_action_performed = True
            return _response(1)
        if path.endswith("/rHisHitchAlarm/cancleSubmitWorkArea"):
            if self.alarm_action_request_error is not None:
                raise self.alarm_action_request_error
            self.alarm_record["isSubmitWorkArea"] = 0
            self.alarm_action_performed = True
            return _response(1)
        if path.endswith("/rHisHitchAlarm/setRtuConductStatusDisposed"):
            if self.alarm_action_request_error is not None:
                raise self.alarm_action_request_error
            self.alarm_record["conductStatue"] = 3
            self.alarm_action_performed = True
            return _response(1)
        if path.endswith("/inspectionTask/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "taskId": "task-1",
                            "taskName": "夜巡一组",
                            "planName": "夜巡计划",
                            "groupName": "一号巡检组",
                            "taskStartDate": "2026-08-01",
                            "taskDeadline": "2026-08-31",
                            "taskState": 2,
                            "taskProgress": "25.00%",
                            "confirmDeviceNum": 4,
                            "lampostQty": 12,
                            "rtuQty": 2,
                        }
                    ],
                    "totalCount": 1,
                }
            )
        if path.endswith("/InspectionDeviceGroup/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "taskId": "task-1",
                            "groupId": "group-day-1",
                            "dateTimeStr": "2026-08-12",
                            "deviceNum": 10,
                            "realityNum": 4,
                            "rate": "40.00%",
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/inspectionTask/getClockinDataByTaskId"):
            return _response(
                {
                    "list": [
                        {
                            "clockinId": "clock-1",
                            "clockinTime": "2026-08-12 20:00:00",
                            "clockinUserName": "巡检员",
                            "deviceCode": "LP-001",
                            "deviceTypeName": "灯杆",
                            "hasIssues": 0,
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/lHisHitchAlarm/getDataByCondition"):
            return _response(
                {
                    "list": [
                        {
                            "hisHitchAlarmId": "leak-1",
                            "alarmAddDate": "2026-08-10 10:00:00",
                            "lampPostCode": "LP-001",
                            "leakageCurrent": 12,
                            "leakageVoltage": 220,
                            "hitchDicName": "漏电告警",
                            "alarmState": 0,
                        }
                    ],
                    "total": 1,
                }
            )
        if path.endswith("/lHisHitchAlarm/getCountDataByCondition"):
            return _response({"todayAlarm": 1, "untreated": 1, "yesterdayAlarm": 0})
        raise AssertionError(f"unexpected API request: {method} {url}")

    def get_http_state(self) -> dict:
        return deepcopy(self.state)

    def set_http_state(self, value: dict) -> None:
        self.state = deepcopy(value)

    @staticmethod
    def assert_post(method: str) -> None:
        if method != "POST":
            raise AssertionError(f"unexpected method: {method}")


def _safe_principal() -> dict:
    return {
        "account": "yanshi",
        "name": "无为",
        "organId": "org-1",
        "organroleId": "role-1",
        "organroleName": "wuwei",
        "personId": "person-1",
        "userId": "user-1",
    }


def _response(payload) -> dict:
    return {
        "status": 200,
        "url": "http://123.232.113.241:4101/smartlight/api",
        "content_type": "application/json",
        "json": payload,
        "text": "",
        "elapsed_ms": 1,
    }


if __name__ == "__main__":
    unittest.main()
