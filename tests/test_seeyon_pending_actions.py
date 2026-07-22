import unittest

from bscli.adapters.seeyon_pending_actions import (
    PendingActionContractMismatch,
    acknowledge_weekly_report,
    approve_efficiency_data,
    pending_action_contract_fingerprint,
    prepare_efficiency_data_approval,
    prepare_standard_collaboration_approval,
    prepare_travel_expense_approval,
    prepare_weekly_report_acknowledgement,
)


class PendingActionTests(unittest.TestCase):
    def test_prepare_efficiency_data_binds_exact_pending_item(self):
        worker = FakeWorker(_fixture("efficiency_data"))
        prepared = prepare_efficiency_data_approval(
            FakeAdapter(worker), worker, _inputs()
        )

        self.assertEqual(prepared["plan"]["business_intent"], "approve_efficiency_data")
        self.assertEqual(prepared["plan"]["target"]["affair_id"], "affair-1")
        self.assertEqual(prepared["plan"]["target"]["summary_id"], "summary-1")
        self.assertNotIn("detail", prepared["plan"])
        self.assertEqual(prepared["summary"]["authorize_label"], "授权审批通过")

    def test_prepare_travel_expense_requires_registered_template_and_form(self):
        fixture = _fixture("travel_expense")
        worker = FakeWorker(fixture)
        prepared = prepare_travel_expense_approval(
            FakeAdapter(worker), worker, _inputs()
        )
        fields = {
            item["label"]: item["value"] for item in prepared["summary"]["fields"]
        }
        self.assertEqual(fields["应付金额合计"], "303.00")
        self.assertEqual(fields["附件数量"], "1")
        self.assertNotIn("收款账号", fields)

        changed = _fixture("travel_expense")
        changed["signals"]["identity"]["form_app_id"] = "changed-form"
        changed_worker = FakeWorker(changed)
        with self.assertRaisesRegex(PendingActionContractMismatch, "form identity"):
            prepare_travel_expense_approval(
                FakeAdapter(changed_worker), changed_worker, _inputs()
            )

    def test_weekly_report_is_acknowledgement_not_approval(self):
        worker = FakeWorker(_fixture("weekly_report"))
        prepared = prepare_weekly_report_acknowledgement(
            FakeAdapter(worker), worker, _inputs(opinion="已阅")
        )
        contract = prepared["plan"]["action_contract"]
        self.assertEqual(contract["action_kind"], "acknowledgement")
        self.assertEqual(prepared["summary"]["authorize_label"], "授权阅办周报")

    def test_standard_collaboration_rejects_specialist_titles_and_fields(self):
        specialist = _fixture("standard_collaboration")
        specialist["source"]["title"] = "【报销】其他报销单-Alice"
        worker = FakeWorker(specialist)
        with self.assertRaisesRegex(PendingActionContractMismatch, "not a registered"):
            prepare_standard_collaboration_approval(
                FakeAdapter(worker), worker, _inputs()
            )

        extra_field = _fixture("standard_collaboration")
        extra_field["detail"]["fields"].append({"name": "金额", "value": "10"})
        worker = FakeWorker(extra_field)
        with self.assertRaisesRegex(PendingActionContractMismatch, "outside"):
            prepare_standard_collaboration_approval(
                FakeAdapter(worker), worker, _inputs()
            )

    def test_commit_acknowledges_and_verifies_pending_disappearance(self):
        fixture = _fixture("weekly_report")
        worker = FakeWorker(fixture)
        adapter = FakeAdapter(worker)
        plan = prepare_weekly_report_acknowledgement(
            adapter, worker, _inputs(opinion="已阅")
        )["plan"]
        boundary = []

        result = acknowledge_weekly_report(
            adapter,
            worker,
            plan,
            enter_commit_boundary=lambda: boundary.append("consumed"),
        )

        self.assertEqual(boundary, ["consumed"])
        self.assertTrue(result["workflow_acknowledged"])
        self.assertTrue(result["verification"]["confirmed"])
        self.assertEqual(worker.page.commit_payload["action_kind"], "acknowledgement")

    def test_commit_approval_sets_approval_result(self):
        fixture = _fixture("efficiency_data")
        worker = FakeWorker(fixture)
        adapter = FakeAdapter(worker)
        plan = prepare_efficiency_data_approval(adapter, worker, _inputs())["plan"]

        result = approve_efficiency_data(
            adapter,
            worker,
            plan,
            enter_commit_boundary=lambda: None,
        )

        self.assertTrue(result["workflow_approved"])
        self.assertEqual(result["workflow_profile"], "efficiency_data")

    def test_commit_rejects_changed_detail_before_boundary(self):
        fixture = _fixture("efficiency_data")
        worker = FakeWorker(fixture)
        adapter = FakeAdapter(worker)
        plan = prepare_efficiency_data_approval(adapter, worker, _inputs())["plan"]
        fixture["detail"]["fields"][0]["value"] = "changed"
        boundary = []

        with self.assertRaisesRegex(PendingActionContractMismatch, "detail_fingerprint"):
            approve_efficiency_data(
                adapter,
                worker,
                plan,
                enter_commit_boundary=lambda: boundary.append("consumed"),
            )
        self.assertEqual(boundary, [])

    def test_contract_fingerprints_are_profile_specific(self):
        fingerprints = {
            pending_action_contract_fingerprint(profile)
            for profile in (
                "efficiency_data",
                "travel_expense",
                "weekly_report",
                "standard_collaboration",
            )
        }
        self.assertEqual(len(fingerprints), 4)


class FakeAdapter:
    def __init__(self, worker):
        self.worker = worker
        self.pending_reads = 0

    def resolve_workflow_detail(self, worker, *, collection, affair_id):
        self.assert_worker(worker)
        if collection != "pending" or affair_id != "affair-1":
            raise AssertionError("unexpected pending target")
        return self.worker.fixture["source"], self.worker.fixture["detail"]

    def list_workflows(self, worker, *, collection, arguments):
        self.assert_worker(worker)
        if collection != "pending" or arguments != {"limit": 100}:
            raise AssertionError("unexpected pending readback")
        self.pending_reads += 1
        return {"items": []}

    def assert_worker(self, worker):
        if worker is not self.worker:
            raise AssertionError("unexpected worker")


class FakeWorker:
    def __init__(self, fixture):
        self.fixture = fixture
        self.page = FakePage(fixture)


class FakePage:
    def __init__(self, fixture):
        self.fixture = fixture
        self.commit_payload = None

    def evaluate(self, _script, argument):
        if isinstance(argument, str):
            return self.fixture["signals"]
        self.commit_payload = dict(argument)
        return {"scheduled": True, "submit_entry": "submitClickFunc"}

    def on(self, _event, _callback):
        return None


def _inputs(**changes):
    return {"affair_id": "affair-1", "opinion": "同意", **changes}


def _fixture(profile):
    fixtures = {
        "efficiency_data": {
            "title": "2026年第29周人工智能研发中心效能数据",
            "fields": [{"name": "接收人", "value": "Alice"}],
            "template_id": "",
            "form_app_id": "",
            "node_policy": "approve",
            "node_policy_name": "审批",
            "attitudes": ["agree", "disagree"],
        },
        "travel_expense": {
            "title": "【报销】差旅费审批报销单-Alice-303.00",
            "fields": [
                {"name": "流水号", "value": "20260722001"},
                {"name": "姓名", "value": "Alice"},
                {"name": "费用归算类型", "value": "部门"},
                {"name": "费用归属部门", "value": "研发中心"},
                {"name": "费用归属事项", "value": "项目"},
                {"name": "关联出差申请单", "value": "出差申请"},
                {"name": "应付金额合计", "value": "303.00"},
                {"name": "收款账号", "value": "6222000000000000"},
            ],
            "template_id": "-2046021869351779722",
            "form_app_id": "-2571419096251022663",
            "node_policy": "报销审批",
            "node_policy_name": "报销审批",
            "attitudes": ["agree", "disagree"],
        },
        "weekly_report": {
            "title": "(自动发起)【综合】周报发送流程-研发中心-28周",
            "fields": [
                {"name": "周报名称", "value": "第28周"},
                {"name": "年度", "value": "2026"},
                {"name": "本周说明", "value": "正常"},
                {"name": "本周 工作总结", "value": "总结"},
                {"name": "下周 工作计划", "value": "计划"},
            ],
            "template_id": "1610567580409022440",
            "form_app_id": "-2351708227632217917",
            "node_policy": "inform",
            "node_policy_name": "知会",
            "attitudes": [],
        },
        "standard_collaboration": {
            "title": "关于征集专家入库工作的通知",
            "fields": [{"name": "接收人", "value": "Alice"}],
            "template_id": "",
            "form_app_id": "",
            "node_policy": "approve",
            "node_policy_name": "审批",
            "attitudes": ["agree", "disagree"],
        },
    }
    selected = fixtures[profile]
    return {
        "source": {
            "affair_id": "affair-1",
            "title": selected["title"],
            "sender": "Sender",
            "date": "2026-07-22",
        },
        "detail": {
            "title": selected["title"],
            "fields": selected["fields"],
            "attachments": (
                [{"name": "receipt.pdf"}] if profile == "travel_expense" else []
            ),
            "workflow": [{"opinion": "submitted"}],
            "actions": [{"code": "ContinueSubmit"}],
        },
        "signals": {
            "affair_matches": True,
            "comment_present": True,
            "submit_present": True,
            "page_path": "/seeyon/collaboration/collaboration.do",
            "node_policy": selected["node_policy"],
            "node_policy_name": selected["node_policy_name"],
            "attitude_codes": selected["attitudes"],
            "identity": {
                "summary_id": "summary-1",
                "process_id": "process-1",
                "template_id": selected["template_id"],
                "form_app_id": selected["form_app_id"],
                "form_record_id": "record-1" if selected["form_app_id"] else "",
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
