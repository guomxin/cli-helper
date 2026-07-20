import json
import unittest

from bscli.adapters.seeyon_submit_phases import (
    SubmissionPhaseTracker,
    pump_browser_events,
)


class SubmissionPhaseTrackerTests(unittest.TestCase):
    def test_tracks_only_sanitized_known_submission_phases(self):
        tracker = SubmissionPhaseTracker()
        tracker.observe_response(
            FakeResponse(
                "http://oa.example.test/seeyon/ajax.do?method=ajaxAction",
                post_data=(
                    "managerName=colManager&"
                    "managerMethod=checkAffairAndLock4NewColJson&secret=form-value"
                ),
            )
        )
        tracker.observe_response(
            FakeResponse(
                "http://oa.example.test/seeyon/ajax.do?method=ajaxAction",
                post_data="managerName=colManager&managerMethod=checkTemplate",
            )
        )
        tracker.observe_response(
            FakeResponse(
                "http://oa.example.test/seeyon/rest/cap4/form/saveOrUpdate",
                post_data="reason=private-value",
            )
        )
        tracker.observe_response(
            FakeResponse(
                "http://oa.example.test/seeyon/collaboration/collaboration.do?method=send",
                post_data="subject=private-value",
            )
        )

        self.assertEqual(
            [item["phase"] for item in tracker.evidence],
            [
                "affair_lock_check",
                "template_check",
                "cap4_form_save",
                "workflow_send",
            ],
        )
        self.assertEqual(tracker.evidence[-1]["operation"], "send")
        self.assertNotIn("private-value", str(tracker.evidence))
        self.assertIn("final workflow send was observed", tracker.unknown_outcome_detail())

    def test_pumps_playwright_events_during_server_readback(self):
        page = FakeWaitPage()

        pump_browser_events(page)

        self.assertEqual(page.waits, [250])

    def test_ignores_unrelated_requests_and_reports_last_safe_phase(self):
        tracker = SubmissionPhaseTracker()
        tracker.observe_response(FakeResponse("http://oa.example.test/seeyon/rest/track/log"))
        tracker.observe_response(
            FakeResponse("http://oa.example.test/seeyon/rest/cap4/form/saveOrUpdate")
        )

        self.assertEqual(len(tracker.evidence), 1)
        self.assertEqual(tracker.evidence[0]["phase"], "cap4_form_save")
        self.assertIn("final workflow send was not observed", tracker.unknown_outcome_detail())

    def test_extracts_only_sanitized_continuable_cap4_validation(self):
        tracker = SubmissionPhaseTracker()
        tracker.observe_response(
            FakeResponse(
                "http://oa.example.test/seeyon/rest/cap4/form/saveOrUpdate",
                payload={
                    "data": {
                        "success": 0,
                        "code": "3003",
                        "data": {
                            "validateResult": json.dumps(
                                {
                                    "ruleError": "<b>请假时长</b>\n  需要确认",
                                    "forceCheck": 0,
                                    "fields": [{"private": "must-not-leak"}],
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                },
            )
        )

        validation = tracker.pending_business_validation
        self.assertEqual(validation["code"], "3003")
        self.assertEqual(validation["message"], "请假时长 需要确认")
        self.assertTrue(validation["can_continue"])
        self.assertTrue(validation["fingerprint"].startswith("sha256:"))
        self.assertNotIn("must-not-leak", str(tracker.evidence))
        self.assertEqual(
            tracker.evidence[-1]["businessStatus"],
            "validation_required",
        )


class FakeWaitPage:
    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


class FakeRequest:
    def __init__(self, *, post_data=""):
        self.method = "POST"
        self.post_data = post_data


class FakeResponse:
    def __init__(self, url, *, status=200, post_data="", payload=None):
        self.url = url
        self.status = status
        self.request = FakeRequest(post_data=post_data)
        self.payload = payload

    def json(self):
        return self.payload


if __name__ == "__main__":
    unittest.main()