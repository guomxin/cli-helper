import json
from urllib.parse import parse_qs
import unittest
from unittest.mock import patch

from bscli.adapters.seeyon_meeting_room_application import (
    MEETING_ROOM_APPLICATION_CANCEL_CONTRACT_VERSION,
    MEETING_ROOM_APPLICATION_CONTRACT_VERSION,
    MeetingRoomApplicationContractMismatch,
    MeetingRoomApplicationOutcomeUnknown,
    build_meeting_room_application_field_card_schema,
    cancel_meeting_room_application,
    create_meeting_room_application,
    meeting_room_application_cancel_contract_fingerprint,
    meeting_room_application_contract_fingerprint,
    normalize_meeting_room_application_inputs,
    prepare_meeting_room_application,
    prepare_meeting_room_application_cancel,
)


class SeeyonMeetingRoomApplicationTests(unittest.TestCase):
    def test_field_card_prefills_user_values_and_live_room_options(self):
        worker = FakeWorker(rooms=[_room(), _room("room-2", "2号会议室")])
        schema = build_meeting_room_application_field_card_schema(
            FakeAdapter(), worker, _inputs(room="三号会议室")
        )

        fields = {item["name"]: item for item in schema["fields"]}
        self.assertEqual(fields["purpose"]["value"], "项目讨论")
        self.assertEqual(fields["room"]["control"], "select")
        self.assertEqual(fields["room"]["value"], "3号会议室")
        self.assertEqual(
            [item["value"] for item in fields["room"]["options"]],
            ["3号会议室", "2号会议室"],
        )
        self.assertEqual(worker.manager_methods, ["roomListInfo"])
        self.assertEqual(worker.mutations, [])

    def test_field_card_only_offers_current_free_rooms(self):
        worker = FakeWorker(
            rooms=[_room(), _room("room-2", "2号会议室")],
            conflict=True,
        )
        schema = build_meeting_room_application_field_card_schema(
            FakeAdapter(), worker, _inputs(room="三号会议室")
        )

        room_field = next(item for item in schema["fields"] if item["name"] == "room")
        self.assertEqual(
            room_field["options"],
            [{"value": "2号会议室", "label": "2号会议室"}],
        )
        self.assertEqual(room_field["value"], "")
        self.assertIn("已占用", schema["notice"])

    def test_prepare_validates_oa_and_freezes_baseline(self):
        worker = FakeWorker(initial_apps=[_application("existing")])
        prepared = prepare_meeting_room_application(
            FakeAdapter(), worker, _inputs()
        )

        self.assertEqual(prepared["plan"]["target"]["room_id"], "room-3")
        self.assertEqual(prepared["plan"]["baseline_application_ids"], ["existing"])
        self.assertFalse(prepared["plan"]["expected_effect"]["meeting_created"])
        self.assertEqual(prepared["summary"]["authorize_label"], "授权申请会议室")
        self.assertEqual(
            worker.manager_methods,
            ["roomListInfo", "validateRoomApps", "getMyApps"],
        )
        self.assertEqual(worker.mutations, [])

    def test_create_rechecks_before_boundary_and_verifies_readback(self):
        events = []
        worker = FakeWorker(events=events)
        result = create_meeting_room_application(
            FakeAdapter(),
            worker,
            _application_plan(),
            enter_commit_boundary=lambda: events.append("authorization-consumed"),
        )

        self.assertEqual(events[:2], ["authorization-consumed", "applyRoom"])
        self.assertTrue(result["meeting_room_application_created"])
        self.assertFalse(result["meeting_created"])
        self.assertEqual(result["application"]["application_id"], "created-app")
        self.assertEqual(result["application"]["description"], "项目讨论")
        self.assertEqual(worker.mutations, ["applyRoom"])

    def test_create_conflict_stops_before_boundary(self):
        boundary = []
        worker = FakeWorker(conflict=True)
        with self.assertRaisesRegex(MeetingRoomApplicationContractMismatch, "occupied"):
            create_meeting_room_application(
                FakeAdapter(),
                worker,
                _application_plan(),
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])
        self.assertEqual(worker.mutations, [])

    def test_create_readback_failure_is_unknown(self):
        boundary = []
        worker = FakeWorker(missing_create_readback=True)
        with self.assertRaises(MeetingRoomApplicationOutcomeUnknown):
            create_meeting_room_application(
                FakeAdapter(),
                worker,
                _application_plan(),
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, ["consumed"])
        self.assertEqual(worker.mutations, ["applyRoom"])

    def test_stale_create_contract_stops_before_boundary(self):
        plan = _application_plan()
        plan["action_contract"]["fingerprint"] = "sha256:stale"
        boundary = []
        with self.assertRaises(MeetingRoomApplicationContractMismatch):
            create_meeting_room_application(
                FakeAdapter(),
                FakeWorker(),
                plan,
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])

    def test_cancel_prepare_freezes_one_own_standalone_application(self):
        worker = FakeWorker(initial_apps=[_application("cancel-me")])
        prepared = prepare_meeting_room_application_cancel(
            FakeAdapter(),
            worker,
            {"application_id": "cancel-me", "cancellation_reason": "计划调整"},
        )

        self.assertEqual(prepared["plan"]["target"]["application_id"], "cancel-me")
        self.assertEqual(prepared["summary"]["title"], "撤销会议室申请")
        self.assertEqual(worker.mutations, [])

    def test_cancel_refuses_application_linked_to_meeting(self):
        worker = FakeWorker(
            initial_apps=[
                _application(
                    "linked",
                    meeting_id="meeting-1",
                    meeting_name="正式会议",
                )
            ]
        )
        with self.assertRaisesRegex(MeetingRoomApplicationContractMismatch, "linked"):
            prepare_meeting_room_application_cancel(
                FakeAdapter(),
                worker,
                {"application_id": "linked", "cancellation_reason": "测试"},
            )

    def test_cancel_refuses_currently_used_or_elapsed_application(self):
        for usage in (1, 2, 3):
            with self.subTest(usage=usage):
                worker = FakeWorker(
                    initial_apps=[_application("blocked", usage_status=usage)]
                )
                with self.assertRaises(MeetingRoomApplicationContractMismatch):
                    prepare_meeting_room_application_cancel(
                        FakeAdapter(),
                        worker,
                        {"application_id": "blocked", "cancellation_reason": "测试"},
                    )

    def test_cancel_crosses_boundary_then_verifies_absence(self):
        events = []
        worker = FakeWorker(
            initial_apps=[_application("cancel-me")],
            events=events,
        )
        result = cancel_meeting_room_application(
            FakeAdapter(),
            worker,
            _cancel_plan(),
            enter_commit_boundary=lambda: events.append("authorization-consumed"),
        )

        self.assertEqual(events[:2], ["authorization-consumed", "cancelRoomApp"])
        self.assertTrue(result["meeting_room_application_canceled"])
        self.assertEqual(
            result["verification"]["state"], "absent_from_my_applications"
        )
        self.assertEqual(worker.mutations, ["cancelRoomApp"])

    def test_cancel_accepts_empty_http_success_then_requires_readback(self):
        worker = FakeWorker(
            initial_apps=[_application("cancel-me")],
            empty_cancel_response=True,
        )

        result = cancel_meeting_room_application(
            FakeAdapter(),
            worker,
            _cancel_plan(),
            enter_commit_boundary=lambda: None,
        )

        self.assertTrue(result["meeting_room_application_canceled"])
        self.assertEqual(
            result["verification"]["state"],
            "absent_from_my_applications",
        )
        self.assertEqual(worker.mutations, ["cancelRoomApp"])

    def test_cancel_empty_http_success_is_unknown_when_readback_stays_active(self):
        worker = FakeWorker(
            initial_apps=[_application("cancel-me")],
            stale_cancel_readback=True,
            empty_cancel_response=True,
        )

        with patch(
            "bscli.adapters.seeyon_meeting_room_application.time.sleep"
        ) as sleep:
            with self.assertRaises(MeetingRoomApplicationOutcomeUnknown):
                cancel_meeting_room_application(
                    FakeAdapter(),
                    worker,
                    _cancel_plan(),
                    enter_commit_boundary=lambda: None,
                )

        self.assertEqual(sleep.call_count, 6)
        self.assertEqual(worker.mutations, ["cancelRoomApp"])

    def test_cancel_uses_room_snapshot_when_my_applications_is_stale(self):
        worker = FakeWorker(
            initial_apps=[_application("cancel-me")],
            stale_cancel_readback=True,
            expose_apps_in_room_snapshot=True,
        )

        result = cancel_meeting_room_application(
            FakeAdapter(),
            worker,
            _cancel_plan(),
            enter_commit_boundary=lambda: None,
        )

        self.assertTrue(result["meeting_room_application_canceled"])
        self.assertEqual(
            result["verification"]["state"],
            "absent_from_room_snapshot",
        )
        self.assertEqual(worker.mutations, ["cancelRoomApp"])

    def test_cancel_detects_target_change_before_boundary(self):
        worker = FakeWorker(initial_apps=[_application("cancel-me", status=1)])
        boundary = []
        with self.assertRaisesRegex(MeetingRoomApplicationContractMismatch, "changed"):
            cancel_meeting_room_application(
                FakeAdapter(),
                worker,
                _cancel_plan(),
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])


class FakeAdapter:
    base_url = "http://oa.example.test/seeyon/"


class FakeWorker:
    def __init__(
        self,
        *,
        rooms=None,
        initial_apps=None,
        conflict=False,
        missing_create_readback=False,
        stale_cancel_readback=False,
        expose_apps_in_room_snapshot=False,
        empty_cancel_response=False,
        events=None,
    ):
        self.rooms = list(rooms) if rooms is not None else [_room()]
        self.apps = list(initial_apps or [])
        self.conflict = conflict
        self.missing_create_readback = missing_create_readback
        self.stale_cancel_readback = stale_cancel_readback
        self.expose_apps_in_room_snapshot = expose_apps_in_room_snapshot
        self.empty_cancel_response = empty_cancel_response
        self.canceled_application_ids = set()
        self.events = events if events is not None else []
        self.manager_methods = []
        self.mutations = []

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=30):
        del method, headers, timeout_seconds
        fields = parse_qs(body or "")
        manager_method = fields["managerMethod"][0]
        arguments = json.loads(fields["arguments"][0])
        self.manager_methods.append(manager_method)
        if manager_method == "roomListInfo":
            bookings = [
                {
                    "appId": item["roomAppId"],
                    "roomId": item["roomId"],
                    "appBeginDate": item["startDatetime"],
                    "appEndDate": item["endDatetime"],
                }
                for item in self.apps
                if item["roomAppId"] not in self.canceled_application_ids
            ] if self.expose_apps_in_room_snapshot else []
            if self.conflict:
                bookings.append({
                    "roomId": "room-3",
                    "appBeginDate": 1784527200000,
                    "appEndDate": 1784534400000,
                })
            return _response({"roomsInfo": self.rooms, "roomAppsInfo": bookings}, url)
        if manager_method == "validateRoomApps":
            return _response({"success": True, "data": []}, url)
        if manager_method == "getMyApps":
            return _response(
                {"data": list(self.apps), "total": len(self.apps), "pages": 1},
                url,
            )
        if manager_method == "applyRoom":
            self.events.append("applyRoom")
            self.mutations.append("applyRoom")
            if not self.missing_create_readback:
                submitted = arguments[0]["roomApps"][0]
                self.apps.append(
                    _application(
                        "created-app",
                        room_id=submitted["roomId"],
                        start_time=submitted["appBeginDate"],
                        end_time=submitted["appEndDate"],
                        description=submitted["description"],
                    )
                )
            return _response({"success": True}, url)
        if manager_method == "cancelRoomApp":
            self.events.append("cancelRoomApp")
            self.mutations.append("cancelRoomApp")
            application_id = arguments[0]["appIds"]
            self.canceled_application_ids.add(application_id)
            if not self.stale_cancel_readback:
                self.apps = [
                    item for item in self.apps
                    if item["roomAppId"] != application_id
                ]
            if self.empty_cancel_response:
                return {
                    "status": 200,
                    "url": url,
                    "json": None,
                    "text": "",
                }
            return _response({"success": True}, url)
        raise AssertionError(f"unexpected method: {manager_method} {arguments}")


def _response(payload, url):
    return {"status": 200, "url": url, "json": payload, "text": json.dumps(payload)}


def _room(room_id="room-3", name="3号会议室"):
    return {"roomId": room_id, "roomName": name, "roomTypeId": "type-1"}


def _application(
    application_id,
    *,
    room_id="room-3",
    status=0,
    usage_status=0,
    meeting_id="",
    meeting_name="",
    start_time=1784527200000,
    end_time=1784534400000,
    description="项目讨论",
):
    return {
        "roomAppId": application_id,
        "roomId": room_id,
        "roomName": "3号会议室",
        "meetingId": meeting_id,
        "meetingName": meeting_name,
        "description": description,
        "startDatetime": start_time,
        "endDatetime": end_time,
        "appStatus": status,
        "appStatusName": "待审核" if status == 0 else "审核通过",
        "usedStatusDisplay": usage_status,
    }


def _inputs(**updates):
    values = {
        "purpose": "项目讨论",
        "room": "3号会议室",
        "start_time": "2026-07-20 14:00",
        "end_time": "2026-07-20 16:00",
    }
    values.update(updates)
    return values


def _application_plan():
    return {
        "business_intent": "apply_meeting_room",
        "target": {"room_id": "room-3", "room_name": "3号会议室"},
        "action_contract": {
            "version": MEETING_ROOM_APPLICATION_CONTRACT_VERSION,
            "fingerprint": meeting_room_application_contract_fingerprint(),
        },
        "exact_input": normalize_meeting_room_application_inputs(_inputs()),
        "baseline_application_ids": [],
    }


def _cancel_plan():
    return {
        "business_intent": "cancel_meeting_room_application",
        "target": {
            "application_id": "cancel-me",
            "room_id": "room-3",
            "room_name": "3号会议室",
            "start_time": "2026-07-20 14:00",
            "end_time": "2026-07-20 16:00",
            "audit_status_code": "0",
            "usage_status_code": "0",
        },
        "action_contract": {
            "version": MEETING_ROOM_APPLICATION_CANCEL_CONTRACT_VERSION,
            "fingerprint": meeting_room_application_cancel_contract_fingerprint(),
        },
        "exact_input": {"cancellation_reason": "计划调整"},
    }


if __name__ == "__main__":
    unittest.main()
