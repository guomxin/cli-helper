import json
from urllib.parse import parse_qs
import unittest

from bscli.adapters.seeyon_meeting import (
    MEETING_CONTRACT_VERSION,
    MeetingContractMismatch,
    MeetingOutcomeUnknown,
    create_meeting,
    meeting_contract_fingerprint,
    normalize_meeting_inputs,
    prepare_meeting_create,
)


class SeeyonMeetingTests(unittest.TestCase):
    def test_normalization_rejects_invalid_time_range(self):
        normalized = normalize_meeting_inputs(_inputs())
        self.assertEqual(normalized["start_time"], "2026-07-20 14:00")
        self.assertEqual(normalized["subject"], "智能体测试")

        with self.assertRaisesRegex(ValueError, "later than"):
            normalize_meeting_inputs(_inputs(end_time="2026-07-20 13:00"))

    def test_prepare_only_runs_read_only_contract_checks(self):
        worker = FakeMeetingWorker()
        prepared = prepare_meeting_create(FakeAdapter(), worker, _inputs())

        self.assertEqual(prepared["plan"]["target"]["room_name"], "3号会议室")
        self.assertTrue(prepared["plan"]["preconditions"]["room_available"])
        self.assertEqual(worker.manager_methods, ["meetingInfo", "roomListInfo", "validateRoomApps"])
        self.assertEqual(worker.mutation_count, 0)

    def test_null_room_validation_data_is_treated_as_no_validation_errors(self):
        worker = FakeMeetingWorker(validation_data=None)
        prepared = prepare_meeting_create(FakeAdapter(), worker, _inputs())
        self.assertTrue(prepared["plan"]["preconditions"]["oa_room_validation_passed"])

    def test_http_200_login_html_is_reported_as_login_required(self):
        from bscli.adapters.seeyon_central import SeeyonLoginRequired
        from bscli.adapters.seeyon_meeting import _response_json

        with self.assertRaises(SeeyonLoginRequired):
            _response_json(
                {
                    "status": 200,
                    "url": "http://oa.example.test/seeyon/main.do?method=login",
                    "json": None,
                    "text": '<input type="password">',
                },
                context="meetingInfo",
            )
    def test_commit_consumes_authorization_before_first_mutation_and_verifies_twice(self):
        events = []
        worker = FakeMeetingWorker(events=events)
        result = create_meeting(
            FakeAdapter(),
            worker,
            _plan(),
            enter_commit_boundary=lambda: events.append("authorization-consumed"),
        )

        self.assertEqual(events[:2], ["authorization-consumed", "content-save"])
        self.assertTrue(result["meeting_created"])
        self.assertTrue(result["meeting_sent"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(
            result["verification"]["methods"],
            ["room_list_readback", "meeting_view_readback"],
        )
        self.assertNotIn("智能体测试", worker.content_save_bodies[0])
        self.assertIn("%5cu667a", worker.content_save_bodies[0].lower())

    def test_room_conflict_blocks_before_authorization_consumption(self):
        worker = FakeMeetingWorker(conflict=True)
        boundary = []
        with self.assertRaisesRegex(MeetingContractMismatch, "occupied"):
            create_meeting(
                FakeAdapter(),
                worker,
                _plan(),
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )

        self.assertEqual(boundary, [])
        self.assertEqual(worker.mutation_count, 0)

    def test_post_boundary_readback_failure_is_unknown(self):
        worker = FakeMeetingWorker(missing_readback=True)
        boundary = []
        with self.assertRaises(MeetingOutcomeUnknown):
            create_meeting(
                FakeAdapter(),
                worker,
                _plan(),
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, ["consumed"])
        self.assertEqual(worker.mutation_count, 2)

    def test_stale_contract_is_rejected_before_consumption(self):
        plan = _plan()
        plan["action_contract"]["fingerprint"] = "sha256:stale"
        boundary = []
        with self.assertRaises(MeetingContractMismatch):
            create_meeting(
                FakeAdapter(),
                FakeMeetingWorker(),
                plan,
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])


class FakeAdapter:
    base_url = "http://oa.example.test/seeyon/"


class FakeMeetingWorker:
    def __init__(self, *, conflict=False, missing_readback=False, events=None, validation_data=...):
        self.conflict = conflict
        self.missing_readback = missing_readback
        self.events = events if events is not None else []
        self.manager_methods = []
        self.mutation_count = 0
        self.content_save_bodies = []
        self.validation_data = [] if validation_data is ... else validation_data
        self.room_list_calls = 0

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=30):
        del method, headers, timeout_seconds
        if "/content/content.do" in url:
            self.events.append("content-save")
            self.mutation_count += 1
            self.content_save_bodies.append(body)
            return _response(
                {
                    "success": True,
                    "contentAll": {"id": "content-1", "moduleId": "temp-1"},
                },
                url,
            )
        fields = parse_qs(body or "")
        manager_method = fields["managerMethod"][0]
        arguments = json.loads(fields["arguments"][0])
        self.manager_methods.append(manager_method)
        if manager_method == "meetingInfo":
            return _response(_meeting_info(), url)
        if manager_method == "validateRoomApps":
            return _response({"success": True, "data": self.validation_data}, url)
        if manager_method == "send":
            self.events.append("meeting-send")
            self.mutation_count += 1
            return _response({"success": True}, url)
        if manager_method == "meetingView":
            return _response({"title": "智能体测试", "body": {"content": ""}}, url)
        if manager_method == "roomListInfo":
            self.room_list_calls += 1
            apps = []
            if self.conflict:
                apps = [_room_booking("occupied")]
            elif self.room_list_calls > 1 and not self.missing_readback:
                apps = [_room_booking("智能体测试", meeting_id="meeting-1")]
            return _response({"roomsInfo": [_room()], "roomAppsInfo": apps}, url)
        raise AssertionError(f"unexpected method: {manager_method} {arguments}")


def _response(payload, url):
    return {"status": 200, "url": url, "json": payload, "text": json.dumps(payload)}


def _meeting_info():
    return {
        "id_temp": "temp-1",
        "currentUser": {"id": "1001"},
        "emceeId": "Member|1001",
        "recorderId": "Member|1001",
        "bodyType": "10",
        "beforeTime": 10,
        "meetingTypes": [{"id": "type-1", "name": "普通会议"}],
    }


def _room():
    return {"roomId": "room-3", "roomName": "3号会议室", "roomTypeId": "type-1"}


def _room_booking(description, *, meeting_id="old-meeting"):
    return {
        "roomId": "room-3",
        "roomName": "3号会议室",
        "appBeginDate": 1784527200000,
        "appEndDate": 1784534400000,
        "description": description,
        "meetingId": meeting_id,
    }


def _inputs(**updates):
    value = {
        "subject": "智能体测试",
        "room": "3号会议室",
        "start_time": "2026-07-20 14:00",
        "end_time": "2026-07-20 16:00",
    }
    value.update(updates)
    return value


def _plan():
    return {
        "business_intent": "create_meeting",
        "target": {"room_id": "room-3", "room_name": "3号会议室"},
        "action_contract": {
            "version": MEETING_CONTRACT_VERSION,
            "fingerprint": meeting_contract_fingerprint(),
        },
        "exact_input": normalize_meeting_inputs(_inputs()),
    }


if __name__ == "__main__":
    unittest.main()
