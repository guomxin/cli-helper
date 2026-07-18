import unittest
from unittest.mock import patch

from bscli.adapters.seeyon_missed_punch import (
    MISSED_PUNCH_APPROVAL_CONTRACT_VERSION,
    MISSED_PUNCH_DRAFT_CONTRACT_VERSION,
    MISSED_PUNCH_FORM_APP_ID,
    MISSED_PUNCH_TEMPLATE_ID,
    MISSED_PUNCH_TEMPLATE_TITLE,
    MissedPunchContractMismatch,
    MissedPunchOutcomeUnknown,
    approve_missed_punch_request,
    missed_punch_approval_contract_fingerprint,
    missed_punch_draft_contract_fingerprint,
    normalize_missed_punch_inputs,
    prepare_missed_punch_approval,
    prepare_missed_punch_draft,
    save_missed_punch_draft,
)


class SeeyonMissedPunchTests(unittest.TestCase):
    def test_normalization_rejects_invalid_reason_and_range(self):
        normalized = normalize_missed_punch_inputs(_inputs())
        self.assertEqual(normalized["reason_type"], "忘记打卡")

        with self.assertRaisesRegex(ValueError, "reason_type"):
            normalize_missed_punch_inputs(_inputs(reason_type="系统故障"))
        with self.assertRaisesRegex(ValueError, "later than"):
            normalize_missed_punch_inputs(_inputs(end_time="2026-07-20 08:00"))

    def test_prepare_draft_freezes_exact_live_contract_without_mutation(self):
        page = FakePage()
        frame = FakeFrame()
        with patch(
            "bscli.adapters.seeyon_missed_punch._open_and_validate_draft_form",
            return_value=(page, frame),
        ):
            prepared = prepare_missed_punch_draft(FakeAdapter(), object(), _inputs())

        self.assertEqual(prepared["plan"]["target"]["template_id"], MISSED_PUNCH_TEMPLATE_ID)
        self.assertEqual(
            prepared["plan"]["form_contract"]["fingerprint"],
            missed_punch_draft_contract_fingerprint(),
        )
        self.assertEqual(prepared["plan"]["expected_effect"]["submitted_count"], 0)
        self.assertEqual(prepared["summary"]["authorize_label"], "授权保存草稿")
        self.assertEqual(page.click_count, 0)

    def test_save_draft_consumes_once_and_never_submits(self):
        page = FakePage()
        frame = FakeFrame()
        expected = normalize_missed_punch_inputs(_inputs())
        readback = {**expected, "subject": "【HR】补签申请单-Alice"}
        boundary = []
        with (
            patch(
                "bscli.adapters.seeyon_missed_punch._open_and_validate_draft_form",
                return_value=(page, frame),
            ),
            patch("bscli.adapters.seeyon_missed_punch._fill_missed_punch_form"),
            patch(
                "bscli.adapters.seeyon_missed_punch._read_missed_punch_form",
                side_effect=[readback, readback],
            ),
            patch(
                "bscli.adapters.seeyon_missed_punch._wait_for_draft_frame",
                return_value=frame,
            ),
            patch("bscli.adapters.seeyon_missed_punch._validate_draft_controls"),
        ):
            result = save_missed_punch_draft(
                FakeAdapter(),
                object(),
                _draft_plan(expected),
                enter_commit_boundary=lambda: boundary.append("consumed"),
                timeout_seconds=5,
            )

        self.assertEqual(boundary, ["consumed"])
        self.assertEqual(page.click_count, 1)
        self.assertTrue(result["draft_saved"])
        self.assertFalse(result["workflow_submitted"])
        self.assertEqual(result["submitted_count"], 0)

    def test_prepare_and_commit_approval_bind_exact_pending_affair(self):
        adapter = FakeAdapter()
        worker = FakeApprovalWorker()
        prepared = prepare_missed_punch_approval(
            adapter,
            worker,
            {"affair_id": "affair-1", "opinion": "同意"},
        )
        self.assertEqual(prepared["plan"]["target"]["affair_id"], "affair-1")
        self.assertEqual(prepared["plan"]["exact_input"]["opinion"], "同意")
        self.assertEqual(prepared["summary"]["authorize_label"], "授权审批通过")
        self.assertIn("立即提交审批通过", prepared["summary"]["authorization_notice"])

        boundary = []
        with patch("bscli.adapters.seeyon_missed_punch.time.sleep", return_value=None):
            result = approve_missed_punch_request(
                adapter,
                worker,
                prepared["plan"],
                enter_commit_boundary=lambda: boundary.append("consumed"),
                timeout_seconds=5,
            )
        self.assertEqual(boundary, ["consumed"])
        self.assertEqual(worker.page.submitted_opinion, "同意")
        self.assertTrue(result["workflow_approved"])
        self.assertEqual(result["verification"]["method"], "pending_disappearance")

    def test_stale_approval_contract_blocks_before_consumption(self):
        plan = _approval_plan()
        plan["action_contract"]["fingerprint"] = "sha256:stale"
        boundary = []
        with self.assertRaises(MissedPunchContractMismatch):
            approve_missed_punch_request(
                FakeAdapter(),
                FakeApprovalWorker(),
                plan,
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])

    def test_post_save_verification_failure_is_unknown(self):
        page = FakePage()
        frame = FakeFrame()
        expected = normalize_missed_punch_inputs(_inputs())
        readback = {**expected, "subject": "Draft"}
        bad = {**readback, "location": "错误地点"}
        with (
            patch(
                "bscli.adapters.seeyon_missed_punch._open_and_validate_draft_form",
                return_value=(page, frame),
            ),
            patch("bscli.adapters.seeyon_missed_punch._fill_missed_punch_form"),
            patch(
                "bscli.adapters.seeyon_missed_punch._read_missed_punch_form",
                side_effect=[readback, bad],
            ),
            patch(
                "bscli.adapters.seeyon_missed_punch._wait_for_draft_frame",
                return_value=frame,
            ),
            patch("bscli.adapters.seeyon_missed_punch._validate_draft_controls"),
        ):
            with self.assertRaises(MissedPunchOutcomeUnknown):
                save_missed_punch_draft(
                    FakeAdapter(),
                    object(),
                    _draft_plan(expected),
                    enter_commit_boundary=lambda: None,
                    timeout_seconds=5,
                )


class FakeAdapter:
    def __init__(self):
        self.pending_reads = 0

    def list_templates(self, _worker):
        return {
            "items": [
                {
                    "title": MISSED_PUNCH_TEMPLATE_TITLE,
                    "template_id": MISSED_PUNCH_TEMPLATE_ID,
                    "form_app_id": MISSED_PUNCH_FORM_APP_ID,
                    "href": "http://oa.example.test/seeyon/collaboration/new",
                }
            ]
        }

    def resolve_workflow_detail(self, _worker, *, collection, affair_id):
        assert collection == "pending"
        assert affair_id == "affair-1"
        return (
            {
                "affair_id": affair_id,
                "title": "【HR】补签申请单-Alice",
                "sender": "Alice",
                "date": "2026-07-20",
            },
            {"actions": [{"code": "ContinueSubmit"}]},
        )

    def list_workflows(self, _worker, *, collection, arguments):
        assert collection == "pending"
        assert arguments == {"limit": 100}
        self.pending_reads += 1
        return {"items": []}


class FakeApprovalWorker:
    def __init__(self):
        self.page = FakeApprovalPage()


class FakeApprovalPage:
    def __init__(self):
        self.submitted_opinion = None

    def evaluate(self, script, argument):
        if isinstance(argument, str):
            return {"affair_matches": True, "comment_present": True, "submit_present": True}
        self.submitted_opinion = argument["opinion"]
        return {"scheduled": True, "submit_entry": "submitClickFunc"}

    def on(self, _event, _handler):
        return None


class FakePage:
    def __init__(self):
        self.url = "http://oa.example.test/seeyon/collaboration/new"
        self.click_count = 0
        self.handlers = {}

    def title(self):
        return "新建页面"

    def on(self, event, handler):
        self.handlers[event] = handler

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_timeout(self, _milliseconds):
        return None

    def wait_for_load_state(self, *_args, **_kwargs):
        return None


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def click(self, **_kwargs):
        assert self.selector == "#saveDraft_a"
        self.page.click_count += 1
        self.page.url = (
            "http://oa.example.test/seeyon/collaboration/collaboration.do?"
            "method=newColl&summaryId=summary-1&affairId=affair-1&from=waitSend"
        )


class FakeFrame:
    url = (
        "http://oa.example.test/seeyon/common/cap4/index.html?"
        f"moduleId={MISSED_PUNCH_TEMPLATE_ID}"
    )


def _inputs(**updates):
    value = {
        "start_time": "2026-07-20 08:30",
        "end_time": "2026-07-20 09:00",
        "location": "公司",
        "reason_type": "忘记打卡",
        "explanation": "早晨忘记打卡",
    }
    value.update(updates)
    return value


def _draft_plan(inputs):
    return {
        "business_intent": "save_missed_punch_request_draft",
        "target": {
            "template_title": MISSED_PUNCH_TEMPLATE_TITLE,
            "template_id": MISSED_PUNCH_TEMPLATE_ID,
            "form_app_id": MISSED_PUNCH_FORM_APP_ID,
        },
        "form_contract": {
            "version": MISSED_PUNCH_DRAFT_CONTRACT_VERSION,
            "fingerprint": missed_punch_draft_contract_fingerprint(),
        },
        "exact_input": inputs,
    }


def _approval_plan():
    return {
        "business_intent": "approve_missed_punch_request",
        "target": {"affair_id": "affair-1", "title": "【HR】补签申请单-Alice"},
        "action_contract": {
            "version": MISSED_PUNCH_APPROVAL_CONTRACT_VERSION,
            "fingerprint": missed_punch_approval_contract_fingerprint(),
        },
        "exact_input": {"opinion": "同意"},
    }


if __name__ == "__main__":
    unittest.main()
