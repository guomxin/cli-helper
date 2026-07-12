import json
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

from bscli.adapters.seeyon_central import (
    SeeyonCentralAdapter,
    SeeyonReadContractMismatch,
    build_central_capability_registry,
)


BASE_URL = "http://oa.example.test/seeyon/main.do?method=main"


class SeeyonCentralWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SeeyonCentralAdapter(base_url=BASE_URL)
        self.worker = FakeWorkflowWorker()

    def test_registry_exposes_complete_central_read_package(self):
        registry = build_central_capability_registry()

        names = [spec.name for spec in registry.list(system="oa")]

        self.assertEqual(
            names,
            [
                "oa.template.list",
                "oa.workflow.detail.get",
                "oa.workflow.done.list",
                "oa.workflow.opinions.list",
                "oa.workflow.pending.list",
                "oa.workflow.tracked.list",
            ],
        )
        self.assertTrue(all(spec.effect == "read" for spec in registry.list(system="oa")))

    def test_pending_list_filters_and_removes_internal_transport_fields(self):
        result = self.adapter.invoke_capability(
            "oa.workflow.pending.list",
            self.worker,
            {"keyword": "Quarterly", "limit": 1},
        )

        self.assertEqual(result["collection"], "pending")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["affair_id"], "pending-1")
        self.assertEqual(result["transport"], "central_http_session")
        serialized = json.dumps(result)
        self.assertNotIn("href", serialized)
        self.assertNotIn("raw_text", serialized)
        self.assertNotIn("owner-secret", serialized)

    def test_done_list_reuses_current_user_contract_and_switches_history_panel(self):
        result = self.adapter.invoke_capability("oa.workflow.done.list", self.worker, {})

        self.assertEqual(result["items"][0]["affair_id"], "done-1")
        self.assertEqual(result["items"][0]["status"], "Alice 待处理")
        arguments = self.worker.last_section_arguments
        self.assertEqual(arguments["ownerId"], "owner-secret")
        self.assertEqual(arguments["spaceId"], "space-current")
        self.assertEqual(arguments["sectionBeanId"], "sentSection")
        self.assertEqual(arguments["entityId"], "sent-entity")
        self.assertEqual(arguments["panelId"], "done-panel")

    def test_detail_merges_same_origin_frame_and_exposes_business_data_only(self):
        result = self.adapter.invoke_capability(
            "oa.workflow.detail.get",
            self.worker,
            {"collection": "done", "affair_id": "done-1", "text_limit": 1000},
        )

        detail = result["detail"]
        self.assertEqual(detail["title"], "Completed request")
        self.assertEqual(detail["fields"], [{"name": "Applicant", "value": "Alice"}])
        self.assertEqual(detail["attachments"], [{"name": "brief.pdf"}])
        self.assertEqual(detail["opinion_count"], 1)
        self.assertIn("Rendered business body", detail["text"])
        serialized = json.dumps(result)
        for forbidden in ("href", "write_hints", "actions", "detail-internal-token"):
            self.assertNotIn(forbidden, serialized)

    def test_opinions_returns_bounded_sanitized_items(self):
        result = self.adapter.invoke_capability(
            "oa.workflow.opinions.list",
            self.worker,
            {"collection": "done", "affair_id": "done-1", "limit": 1},
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [{"text": "Opinion approved by Alice 2026-07-10 10:30"}])
        self.assertEqual(result["transport"], "central_browser_session")

    def test_invalid_collection_is_rejected_before_browser_navigation(self):
        with self.assertRaisesRegex(ValueError, "collection must be one of"):
            self.adapter.invoke_capability(
                "oa.workflow.detail.get",
                self.worker,
                {"collection": "sent", "affair_id": "done-1"},
            )

        self.assertEqual(self.worker.goto_calls, [])

    def test_empty_section_shell_is_not_misreported_as_an_empty_collection(self):
        self.worker.section_payload_override = {}

        with self.assertRaisesRegex(SeeyonReadContractMismatch, "missing Data"):
            self.adapter.invoke_capability("oa.workflow.pending.list", self.worker, {})


class FakeWorkflowWorker:
    def __init__(self):
        self.page = FakeWorkflowPage()
        self.goto_calls = []
        self.render_calls = []
        self.last_section_arguments = {}
        self.section_payload_override = None
        self._resource_urls = [
            _section_url("pendingSection", entity_id="pending-entity", panel_id="pending-panel"),
            _section_url("sentSection", entity_id="sent-entity", panel_id="sent-panel"),
        ]

    @property
    def page_url(self):
        return self.page.url

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.page.url = url
        return self.page

    def resource_urls(self):
        return list(self._resource_urls)

    def request(self, _method, url, **_kwargs):
        if "/rest/template/myTemplate" in url:
            return {
                "status": 200,
                "url": url,
                "content_type": "application/json",
                "json": {"code": 0, "data": {"templates": []}},
                "text": "",
            }
        arguments = _arguments_from_url(url)
        self.last_section_arguments = arguments
        if arguments.get("sectionBeanId") == "pendingSection":
            projection = _projection("pending-1", "Quarterly report", pending=True)
        elif arguments.get("panelId") == "done-panel":
            projection = _projection("done-1", "Completed request")
        elif arguments.get("panelId") == "tracked-panel":
            projection = _projection("tracked-1", "Tracked request")
        else:
            projection = _projection("sent-1", "Sent request")
        if self.section_payload_override is not None:
            projection = self.section_payload_override
        return {
            "status": 200,
            "url": url,
            "content_type": "application/json",
            "json": projection,
            "text": "",
        }

    def rendered_snapshot(self, url, **_kwargs):
        self.render_calls.append(url)
        return {
            "url": "http://oa.example.test/seeyon/detail?detail-internal-token=secret",
            "title": "Completed request",
            "html": """
                <html><body>
                  <h1>Completed request Alice 2026-07-10</h1>
                  <div class="processLog">Opinion approved by Alice 2026-07-10 10:30</div>
                </body></html>
            """,
            "frames": [
                {
                    "url": "http://oa.example.test/seeyon/cap4",
                    "html": """
                        <html><body>
                          <table><tr><th>Applicant</th><td>Alice</td></tr></table>
                          <div>Rendered business body</div>
                          <a href="/seeyon/fileUpload.do?method=download&fileId=secret">brief.pdf</a>
                          <script>var jsonArrBase = '[{"codes":["ContinueSubmit"]}]';</script>
                        </body></html>
                    """,
                }
            ],
        }


class FakeWorkflowPage:
    def __init__(self):
        self.url = BASE_URL

    def content(self):
        return """
            <div id="section_sent-entity">
              <ul>
                <li id="sectionName_sent-panel" title="\u5df2\u53d1\u4e8b\u9879">\u5df2\u53d1\u4e8b\u9879</li>
                <li id="sectionName_done-panel" title="\u5df2\u529e\u4e8b\u9879">\u5df2\u529e\u4e8b\u9879</li>
                <li id="sectionName_tracked-panel" title="\u8ddf\u8e2a\u4e8b\u9879">\u8ddf\u8e2a\u4e8b\u9879</li>
              </ul>
            </div>
        """


def _section_url(section_bean_id, *, entity_id, panel_id):
    arguments = {
        "sectionBeanId": section_bean_id,
        "entityId": entity_id,
        "panelId": panel_id,
        "ownerId": "owner-secret",
        "spaceId": "space-current",
    }
    return "http://oa.example.test/seeyon/ajax.do?" + urlencode(
        {
            "method": "ajaxAction",
            "managerName": "sectionManager",
            "managerMethod": "doProjection",
            "arguments": json.dumps(arguments, separators=(",", ":")),
        }
    )


def _arguments_from_url(url):
    raw = parse_qs(urlparse(url).query).get("arguments", ["{}"])[0]
    return json.loads(raw)


def _projection(affair_id, title, *, pending=False):
    cells = [
        {
            "cellContentHTML": title,
            "id": affair_id,
            "linkURL": (
                "/seeyon/collaboration/collaboration.do"
                f"?method=summary&affairId={affair_id}&internal=secret"
            ),
            "className": "ReadDifferFromNotRead" if pending else "",
        },
        {"cellContentHTML": "Alice" if pending else "Alice&nbsp待处理"},
        {"cellContentHTML": "2026-07-10"},
        {"cellContentHTML": "Collaboration"},
    ]
    return {
        "Name": "section",
        "Data": {"dataCount": 1, "pageNo": 1, "rows": [{"cells": cells}]},
    }


if __name__ == "__main__":
    unittest.main()
