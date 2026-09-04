import json
from urllib.parse import parse_qs
import unittest

from bscli.adapters.seeyon_meeting_room import (
    MeetingRoomContractMismatch,
    list_meeting_room_availability,
    list_my_meeting_room_applications,
)


class SeeyonMeetingRoomTests(unittest.TestCase):
    def test_availability_returns_booking_owner_without_meeting_details(self):
        worker = FakeMeetingRoomWorker()

        result = list_meeting_room_availability(
            FakeAdapter(),
            worker,
            {
                "start_time": "2026-07-20 14:00",
                "end_time": "2026-07-20 16:00",
            },
        )

        self.assertTrue(result["availability_evaluated"])
        self.assertEqual(result["summary"]["available_count"], 1)
        self.assertEqual(result["summary"]["occupied_count"], 1)
        occupied = next(room for room in result["rooms"] if room["room_id"] == "room-3")
        self.assertFalse(occupied["available"])
        self.assertEqual(occupied["capacity"], 18)
        self.assertTrue(occupied["requires_approval"])
        self.assertEqual(occupied["busy_intervals"][0]["status_label"], "已审核")
        self.assertEqual(
            occupied["busy_intervals"][0]["booked_by_name"],
            "张三",
        )
        self.assertEqual(
            occupied["busy_intervals"][0]["booked_by_department"],
            "研发中心",
        )
        self.assertNotIn("meeting_name", occupied["busy_intervals"][0])
        self.assertNotIn("person-secret", json.dumps(result, ensure_ascii=False))
        self.assertEqual(worker.methods, ["roomListInfo"])
        self.assertEqual(
            worker.arguments[0],
            [{
                "roomName": "",
                "sortType": "-1",
                "startDatetime": 1784527200000,
                "endDatetime": 1784534400000,
            }],
        )

    def test_availability_filters_free_rooms_and_capacity(self):
        result = list_meeting_room_availability(
            FakeAdapter(),
            FakeMeetingRoomWorker(),
            {
                "start_time": "2026-07-20 14:00",
                "end_time": "2026-07-20 16:00",
                "minimum_capacity": 20,
                "only_available": True,
            },
        )

        self.assertEqual([room["room_name"] for room in result["rooms"]], ["4层2#会议室"])

    def test_room_directory_without_time_does_not_claim_availability(self):
        result = list_meeting_room_availability(
            FakeAdapter(),
            FakeMeetingRoomWorker(),
            {"room_name": "会议室"},
        )

        self.assertFalse(result["availability_evaluated"])
        self.assertTrue(all(room["available"] is None for room in result["rooms"]))
        self.assertTrue(all(not room["busy_intervals"] for room in result["rooms"]))

    def test_availability_requires_complete_interval(self):
        with self.assertRaisesRegex(ValueError, "supplied together"):
            list_meeting_room_availability(
                FakeAdapter(),
                FakeMeetingRoomWorker(),
                {"start_time": "2026-07-20 14:00"},
            )
        with self.assertRaisesRegex(ValueError, "only_available"):
            list_meeting_room_availability(
                FakeAdapter(),
                FakeMeetingRoomWorker(),
                {"only_available": True},
            )

    def test_my_applications_pushes_filters_and_normalizes_status(self):
        worker = FakeMeetingRoomWorker()

        result = list_my_meeting_room_applications(
            FakeAdapter(),
            worker,
            {
                "room_name": "3号",
                "application_start_date": "2026-07-01",
                "application_end_date": "2026-07-31",
                "audit_status": "approved",
                "limit": 50,
            },
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["audit_status"], "approved")
        self.assertEqual(result["items"][0]["audit_status_label"], "审核通过")
        self.assertEqual(result["items"][0]["applied_at"], "2026-07-18 09:30")
        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(
            worker.arguments[0][1],
            {
                "roomName": "3号",
                "beginDate": "2026-07-01 00:00",
                "endDate": "2026-07-31 23:59",
                "status": "1",
            },
        )

    def test_my_applications_rejects_invalid_date_range(self):
        with self.assertRaisesRegex(ValueError, "cannot be after"):
            list_my_meeting_room_applications(
                FakeAdapter(),
                FakeMeetingRoomWorker(),
                {
                    "application_start_date": "2026-08-01",
                    "application_end_date": "2026-07-01",
                },
            )

    def test_backend_contract_error_is_not_treated_as_empty_data(self):
        worker = FakeMeetingRoomWorker(error={"code": "BAD", "message": "contract changed"})
        with self.assertRaises(MeetingRoomContractMismatch):
            list_meeting_room_availability(FakeAdapter(), worker, {})


class FakeAdapter:
    base_url = "http://oa.example.test/seeyon/"


class FakeMeetingRoomWorker:
    def __init__(self, *, error=None):
        self.methods = []
        self.arguments = []
        self.error = error

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=30):
        del method, headers, timeout_seconds
        fields = parse_qs(body or "")
        manager_method = fields["managerMethod"][0]
        arguments = json.loads(fields["arguments"][0])
        self.methods.append(manager_method)
        self.arguments.append(arguments)
        if self.error is not None:
            payload = self.error
        elif manager_method == "roomListInfo":
            payload = {
                "roomsInfo": [
                    {
                        "roomId": "room-3",
                        "roomName": "4层3#会议室",
                        "roomTypeId": "type-1",
                        "seatCount": 18,
                        "needApp": 1,
                    },
                    {
                        "roomId": "room-2",
                        "roomName": "4层2#会议室",
                        "roomTypeId": "type-1",
                        "seatCount": 30,
                        "needApp": 0,
                    },
                ],
                "roomAppsInfo": [
                    {
                        "roomId": "room-3",
                        "appBeginDate": 1784529000000,
                        "appEndDate": 1784532600000,
                        "status": "1",
                        "statusName": "已审核",
                        "meetingName": "private subject",
                        "perName": "张三",
                        "perDeptName": "研发中心",
                        "perId": "person-secret",
                    }
                ],
            }
        elif manager_method == "getMyApps":
            payload = {
                "page": 1,
                "pages": 1,
                "size": 50,
                "total": 1,
                "data": [
                    {
                        "roomAppId": "app-1",
                        "roomId": "room-3",
                        "roomName": "3号会议室",
                        "roomSeatCount": 18,
                        "adminNames": "管理员",
                        "meetingId": "meeting-1",
                        "meetingName": "项目讨论",
                        "appDatetime": "2026-07-18 09:30:00",
                        "startDatetime": "2026-07-20 14:00:00",
                        "endDatetime": "2026-07-20 16:00:00",
                        "appStatus": 1,
                        "appStatusName": "审核通过",
                        "usedStatus": 0,
                        "usedStatusName": "未使用",
                    }
                ],
            }
        else:
            raise AssertionError(f"unexpected manager method: {manager_method}")
        return {
            "status": 200,
            "url": url,
            "json": payload,
            "text": json.dumps(payload, ensure_ascii=False),
        }


if __name__ == "__main__":
    unittest.main()
