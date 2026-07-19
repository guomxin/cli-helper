import unittest
from unittest.mock import patch

from bscli.adapters.seeyon_business_trip import (
    BUSINESS_TRIP_FORM_APP_ID,
    BUSINESS_TRIP_TEMPLATE_ID,
    BUSINESS_TRIP_TEMPLATE_TITLE,
    BusinessTripContractMismatch,
    BusinessTripOutcomeUnknown,
    normalize_business_trip_inputs,
)
from bscli.adapters.seeyon_business_trip_submit import (
    BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION,
    business_trip_submit_contract_fingerprint,
    prepare_business_trip_submission,
    submit_business_trip_request,
)


class SeeyonBusinessTripSubmitTests(unittest.TestCase):
    def test_prepare_freezes_subject_and_sent_baseline_without_clicking(self):
        page = FakePage()
        frame = FakeFrame()
        expected = normalize_business_trip_inputs(_inputs())
        readback = {**expected, "note": "", "subject": "【HR】出差申请单-Alice-青岛"}
        adapter = FakeAdapter(sent_items=[{"affair_id": "sent-old", "title": "Old"}])
        with (
            patch(
                "bscli.adapters.seeyon_business_trip_submit._open_and_validate_form",
                return_value=(page, frame),
            ),
            patch("bscli.adapters.seeyon_business_trip_submit._validate_optional_inputs"),
            patch("bscli.adapters.seeyon_business_trip_submit._fill_business_trip_form"),
            patch(
                "bscli.adapters.seeyon_business_trip_submit._read_business_trip_form",
                return_value=readback,
            ),
        ):
            prepared = prepare_business_trip_submission(adapter, object(), _inputs())

        plan = prepared["plan"]
        self.assertEqual(plan["target"]["expected_subject"], readback["subject"])
        self.assertEqual(plan["sent_baseline_affair_ids"], ["sent-old"])
        self.assertTrue(plan["expected_effect"]["workflow_submitted"])
        self.assertEqual(prepared["summary"]["authorize_label"], "授权提交审批")
        self.assertIn("正式提交", prepared["summary"]["authorization_notice"])
        self.assertEqual(page.click_count, 0)

    def test_commit_consumes_authorization_before_send_and_verifies_sent_readback(self):
        page = FakePage()
        frame = FakeFrame()
        expected = normalize_business_trip_inputs(_inputs())
        subject = "【HR】出差申请单-Alice-青岛"
        readback = {**expected, "note": "", "subject": subject}
        boundary = []
        with (
            patch(
                "bscli.adapters.seeyon_business_trip_submit._open_and_validate_form",
                return_value=(page, frame),
            ),
            patch("bscli.adapters.seeyon_business_trip_submit._validate_optional_inputs"),
            patch("bscli.adapters.seeyon_business_trip_submit._fill_business_trip_form"),
            patch(
                "bscli.adapters.seeyon_business_trip_submit._read_business_trip_form",
                return_value=readback,
            ),
            patch(
                "bscli.adapters.seeyon_business_trip_submit._wait_for_sent_readback",
                return_value={
                    "affair_id": "sent-new",
                    "title": subject,
                    "state": "sent",
                    "detail_readable": True,
                    "field_count": 8,
                },
            ),
        ):
            result = submit_business_trip_request(
                FakeAdapter(),
                object(),
                _plan(expected, subject),
                enter_commit_boundary=lambda: boundary.append(page.click_count),
                timeout_seconds=5,
            )

        self.assertEqual(boundary, [0])
        self.assertEqual(page.click_count, 1)
        self.assertTrue(result["workflow_submitted"])
        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["submitted"]["affair_id"], "sent-new")
        self.assertEqual(result["request_evidence"][0]["method"], "POST")

    def test_post_send_readback_failure_is_outcome_unknown(self):
        page = FakePage()
        frame = FakeFrame()
        expected = normalize_business_trip_inputs(_inputs())
        subject = "【HR】出差申请单-Alice-青岛"
        readback = {**expected, "note": "", "subject": subject}
        boundary = []
        with (
            patch(
                "bscli.adapters.seeyon_business_trip_submit._open_and_validate_form",
                return_value=(page, frame),
            ),
            patch("bscli.adapters.seeyon_business_trip_submit._validate_optional_inputs"),
            patch("bscli.adapters.seeyon_business_trip_submit._fill_business_trip_form"),
            patch(
                "bscli.adapters.seeyon_business_trip_submit._read_business_trip_form",
                return_value=readback,
            ),
            patch(
                "bscli.adapters.seeyon_business_trip_submit._wait_for_sent_readback",
                side_effect=BusinessTripOutcomeUnknown("not confirmed"),
            ),
        ):
            with self.assertRaises(BusinessTripOutcomeUnknown):
                submit_business_trip_request(
                    FakeAdapter(),
                    object(),
                    _plan(expected, subject),
                    enter_commit_boundary=lambda: boundary.append("consumed"),
                    timeout_seconds=5,
                )

        self.assertEqual(boundary, ["consumed"])
        self.assertEqual(page.click_count, 1)

    def test_stale_submit_plan_is_rejected_before_authorization_consumption(self):
        expected = normalize_business_trip_inputs(_inputs())
        plan = _plan(expected, "Subject")
        plan["form_contract"]["fingerprint"] = "sha256:stale"
        boundary = []

        with self.assertRaises(BusinessTripContractMismatch):
            submit_business_trip_request(
                FakeAdapter(),
                object(),
                plan,
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )

        self.assertEqual(boundary, [])


class FakeAdapter:
    def __init__(self, sent_items=None):
        self.sent_items = list(sent_items or [])

    def list_templates(self, _worker):
        return {
            "items": [
                {
                    "title": BUSINESS_TRIP_TEMPLATE_TITLE,
                    "template_id": BUSINESS_TRIP_TEMPLATE_ID,
                    "form_app_id": BUSINESS_TRIP_FORM_APP_ID,
                    "href": "http://oa.example.test/seeyon/collaboration/new",
                }
            ]
        }

    def list_workflows(self, _worker, *, collection, arguments):
        if collection != "sent" or arguments != {"limit": 100}:
            raise AssertionError("unexpected sent-list lookup")
        return {"items": self.sent_items}


class FakePage:
    def __init__(self):
        self.click_count = 0
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def click(self, **_kwargs):
        if self.selector != "#sendId_a":
            raise AssertionError(f"unexpected click: {self.selector}")
        self.page.click_count += 1
        response_handler = self.page.handlers.get("response")
        if response_handler:
            response_handler(FakeResponse())


class FakeRequest:
    method = "POST"


class FakeResponse:
    url = "http://oa.example.test/seeyon/collaboration/collaboration.do?method=send"
    status = 200
    request = FakeRequest()


class FakeFrame:
    pass


def _inputs():
    return {
        "start_time": "2026-07-21 09:00",
        "end_time": "2026-07-21 18:00",
        "travel_mode": "火车",
        "origin": "济南",
        "destination": "青岛",
        "reason": "客户交流",
        "has_direct_supervisor": False,
    }


def _plan(inputs, subject):
    return {
        "business_intent": "submit_business_trip_request",
        "target": {
            "template_title": BUSINESS_TRIP_TEMPLATE_TITLE,
            "template_id": BUSINESS_TRIP_TEMPLATE_ID,
            "form_app_id": BUSINESS_TRIP_FORM_APP_ID,
            "expected_subject": subject,
        },
        "form_contract": {
            "version": BUSINESS_TRIP_SUBMIT_CONTRACT_VERSION,
            "fingerprint": business_trip_submit_contract_fingerprint(),
        },
        "exact_input": inputs,
        "sent_baseline_affair_ids": ["sent-old"],
    }


if __name__ == "__main__":
    unittest.main()
