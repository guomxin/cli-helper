import json
import unittest

from bscli.adapters.seeyon_home import (
    parse_navigation_inventory,
    parse_oa_detail,
    parse_pending_list,
    parse_pending_projection,
    parse_sent_projection,
    parse_template_list,
    parse_template_projection,
)


class SeeyonHomeParserTests(unittest.TestCase):
    def test_parse_oa_detail_extracts_fields_attachments_and_workflow(self):
        html = """
        <html>
          <head><title>Seal request - OA</title></head>
          <body>
            <h1 id="summarySubject">Seal request</h1>
            <table id="formData">
              <tr><th>Applicant</th><td>Alice</td></tr>
              <tr><td>Department</td><td>Finance</td></tr>
            </table>
            <div class="content">Please approve the company seal usage.</div>
            <div id="attachments">
              <a href="/seeyon/fileUpload.do?method=download&fileId=f1">seal-plan.pdf</a>
            </div>
            <table class="processLog">
              <tr><td>Node</td><td>Manager approval</td><td>Opinion: approved</td></tr>
            </table>
          </body>
        </html>
        """

        result = parse_oa_detail(
            html,
            base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary",
        )

        self.assertEqual(result["title"], "Seal request")
        self.assertIn("Please approve", result["text"])
        self.assertEqual(
            result["fields"],
            [
                {"name": "Applicant", "value": "Alice"},
                {"name": "Department", "value": "Finance"},
            ],
        )
        self.assertEqual(result["attachments"][0]["name"], "seal-plan.pdf")
        self.assertEqual(
            result["attachments"][0]["href"],
            "http://10.10.50.110/seeyon/fileUpload.do?method=download&fileId=f1",
        )
        self.assertEqual(result["workflow"][0]["text"], "Node Manager approval Opinion: approved")

    def test_parse_oa_detail_omits_script_and_style_text_from_visible_text(self):
        html = """
        <html>
          <head>
            <title>HR handover - OA</title>
            <style>.secret { color: red; }</style>
            <script>
              var _sessionid = 'SECRET_SESSION_ID';
              var jsonArrBase = '[{"codes":["ContinueSubmit"],"label":"Submit"}]';
            </script>
          </head>
          <body>
            <h1 id="summarySubject">HR handover</h1>
            <div class="content">Please review the handover checklist.</div>
          </body>
        </html>
        """

        result = parse_oa_detail(
            html,
            base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary",
        )

        self.assertIn("Please review the handover checklist.", result["text"])
        self.assertNotIn("_sessionid", result["text"])
        self.assertNotIn("SECRET_SESSION_ID", result["text"])
        self.assertNotIn("jsonArrBase", result["text"])
        self.assertNotIn(".secret", result["text"])
        self.assertEqual(result["actions"][0]["code"], "ContinueSubmit")

    def test_parse_oa_detail_filters_script_like_workflow_noise(self):
        html = """
        <html>
          <body>
            <h1 id="summarySubject">Archive confirmation</h1>
            <div class="workflowAdvanced">
              <!-- var workflowAdvanced = true; //-->
              {{# var prediction = d; }}
              <script>var jsonArrBase = '[{"codes":["Opinion"],"label":"意见"}]';</script>
              function attDivToggle() { return false; }
            </div>
            <div class="processLog">处理人意见区 （共1条，0个赞） 与我相关 （共0条）</div>
            <div class="processLog">处理后归档</div>
            <div class="processLog">流程</div>
            <div class="processLog">Archive confirmation 王玉霄 2026-06-15 15:53 表单 流程 取消 修改流程</div>
            <div class="processLog">王玉霄 已阅 2026-06-15 16:06 回复 ( ) 0</div>
            <div class="processLog">王玉霄 已阅 2026-06-15 16:06 回复 ( ) 0</div>
            <div class="processLog">意见隐藏 不包括: 跟踪 全部 指定人 处理后归档</div>
          </body>
        </html>
        """

        result = parse_oa_detail(
            html,
            base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary",
        )

        self.assertEqual(
            result["workflow"],
            [
                {
                    "text": "王玉霄 已阅 2026-06-15 16:06",
                    "handler": "王玉霄",
                    "opinion": "已阅",
                    "time": "2026-06-15 16:06",
                }
            ],
        )
        self.assertEqual(result["workflow_count"], 1)

    def test_parse_oa_detail_splits_aggregated_workflow_opinions(self):
        html = """
        <html>
          <body>
            <div class="processLog">
              黄佳豪 已阅 2026-06-18 09:51 回复 ( ) 0
              杨宏博 同意 2026-06-18 09:54 回复 ( ) 0
              王玉霄 同意 2026-06-18 10:01 回复 ( ) 0
            </div>
            <div class="processLog">黄佳豪 已阅 2026-06-18 09:51 回复 ( ) 0</div>
          </body>
        </html>
        """

        result = parse_oa_detail(
            html,
            base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary",
        )

        self.assertEqual(
            result["workflow"],
            [
                {
                    "text": "黄佳豪 已阅 2026-06-18 09:51",
                    "handler": "黄佳豪",
                    "opinion": "已阅",
                    "time": "2026-06-18 09:51",
                },
                {
                    "text": "杨宏博 同意 2026-06-18 09:54",
                    "handler": "杨宏博",
                    "opinion": "同意",
                    "time": "2026-06-18 09:54",
                },
                {
                    "text": "王玉霄 同意 2026-06-18 10:01",
                    "handler": "王玉霄",
                    "opinion": "同意",
                    "time": "2026-06-18 10:01",
                },
            ],
        )

    def test_parse_oa_detail_extracts_write_actions_from_page_script(self):
        html = """
        <html>
          <body>
            <h1 id="summarySubject">Contract archive</h1>
            <script>
              var jsonArrBase = '[{"codes":["ContinueSubmit"],"label":"提交","id":"ContinueSubmit"},{"codes":["Opinion"],"label":"意见","id":"Opinion"},{"codes":["Archive"],"label":"处理后归档","id":"Archive"}]';
              var CSRFTOKEN = 'csrf-from-page';
            </script>
            <input type="hidden" name="contentAffairId" value="affair-1">
          </body>
        </html>
        """

        result = parse_oa_detail(html, base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do")

        self.assertEqual(result["action_count"], 3)
        self.assertEqual(
            result["actions"][0],
            {
                "code": "ContinueSubmit",
                "label": "提交",
                "id": "ContinueSubmit",
                "access": "write",
                "risk": "high",
                "requires_confirmation": True,
                "supports_dry_run": True,
                "source": "jsonArrBase",
            },
        )
        self.assertEqual(result["actions"][1]["code"], "Opinion")
        self.assertEqual(result["actions"][1]["risk"], "medium")
        self.assertEqual(
            result["write_hints"],
            {
                "csrf_tokens": [{"name": "CSRFTOKEN", "value_present": True}],
                "hidden_fields": [{"name": "contentAffairId", "value_present": True}],
            },
        )

    def test_parse_oa_detail_extracts_write_endpoint_candidates(self):
        html = """
        <html>
          <body>
            <script>
              function submitOpinion() {
                $.ajax({url:'/seeyon/ajax.do?method=submitOpinion'});
                return '/collaboration/collaboration.do?method=finishWorkItem&from=listPending';
              }
              var readOnlyContent = '/content/content.do?method=index&canDeleteISigntureHtml=true';
            </script>
            <input type="hidden" name="contentAffairId" value="affair-1">
            <input type="hidden" name="summaryId" value="summary-1">
          </body>
        </html>
        """

        result = parse_oa_detail(
            html,
            base_url="http://10.10.50.110/seeyon/collaboration/collaboration.do",
        )

        self.assertEqual(
            result["write_hints"]["hidden_fields"],
            [
                {"name": "contentAffairId", "value_present": True},
                {"name": "summaryId", "value_present": True},
            ],
        )
        self.assertEqual(
            result["write_hints"]["endpoint_candidates"],
            [
                {
                    "url": "http://10.10.50.110/seeyon/ajax.do?method=submitOpinion",
                    "method": "UNKNOWN",
                    "risk": "high",
                    "source": "rendered_html",
                    "tested": False,
                },
                {
                    "url": "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=finishWorkItem&from=listPending",
                    "method": "UNKNOWN",
                    "risk": "high",
                    "source": "rendered_html",
                    "tested": False,
                },
            ],
        )

    def test_parse_pending_list_extracts_structured_rows(self):
        html = """
        <div id="section_556815601453123423">
          <table>
            <tr>
              <td>
                <a class="cellContentText" title="Weekly report"
                   onclick="checkAndOpenLink('/collaboration/collaboration.do?method=summary&amp;affairId=abc-123&amp;showTab=true')">
                  <span class="titleText">Weekly report</span>
                </a>
              </td>
              <td>Alice</td>
              <td>Today 08:57</td>
              <td>Collaboration</td>
            </tr>
            <tr class="AlreadyRead">
              <td>
                <a class="cellContentText" title="Contract archive"
                   onclick="checkAndOpenLink('/collaboration/collaboration.do?method=summary&amp;affairId=def-456&amp;showTab=true')">
                  <span class="titleText">Contract archive</span>
                </a>
              </td>
              <td>Bob</td>
              <td>2026-06-15</td>
              <td>Collaboration</td>
            </tr>
          </table>
        </div>
        """

        result = parse_pending_list(html, base_url="http://10.10.50.110/seeyon/main.do?method=main")

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"][0]["title"], "Weekly report")
        self.assertEqual(result["items"][0]["sender"], "Alice")
        self.assertEqual(result["items"][0]["date"], "Today 08:57")
        self.assertEqual(result["items"][0]["category"], "Collaboration")
        self.assertEqual(result["items"][0]["affair_id"], "abc-123")
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=abc-123&showTab=true",
        )
        self.assertFalse(result["items"][0]["read"])
        self.assertTrue(result["items"][1]["read"])

    def test_parse_pending_projection_extracts_rows_from_section_api(self):
        projection = {
            "Name": "全部待办",
            "Data": {
                "dataCount": 1,
                "pageNo": 1,
                "rows": [
                    {
                        "cells": [
                            {
                                "cellContentHTML": "Weekly report",
                                "id": "abc-123",
                                "linkURL": "/collaboration/collaboration.do?method=summary&affairId=abc-123&showTab=true",
                                "className": "ReadDifferFromNotRead",
                            },
                            {"cellContentHTML": "Alice", "alt": "Alice"},
                            {"cellContentHTML": "Today 08:57"},
                            {"cellContentHTML": "Collaboration"},
                        ]
                    }
                ],
            },
        }

        result = parse_pending_projection(
            projection,
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
        )

        self.assertEqual(result["source"], "section_api")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Weekly report")
        self.assertEqual(result["items"][0]["sender"], "Alice")
        self.assertEqual(result["items"][0]["date"], "Today 08:57")
        self.assertEqual(result["items"][0]["category"], "Collaboration")
        self.assertEqual(result["items"][0]["affair_id"], "abc-123")
        self.assertFalse(result["items"][0]["read"])
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&affairId=abc-123&showTab=true",
        )

    def test_parse_sent_projection_extracts_rows_from_section_api(self):
        projection = {
            "Name": "已发事项",
            "Data": {
                "dataCount": 1,
                "pageNo": 1,
                "rows": [
                    {
                        "cells": [
                            {
                                "cellContentHTML": "Seal request",
                                "id": "sent-123",
                                "linkURL": "/collaboration/collaboration.do?method=summary&openFrom=listSent&affairId=sent-123&showTab=true",
                            },
                            {"cellContentHTML": "已结束"},
                            {"cellContentHTML": "2026-06-15"},
                            {"cellContentHTML": "协同"},
                        ]
                    }
                ],
            },
        }

        result = parse_sent_projection(
            projection,
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
        )

        self.assertEqual(result["source"], "section_api")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Seal request")
        self.assertEqual(result["items"][0]["status"], "已结束")
        self.assertEqual(result["items"][0]["date"], "2026-06-15")
        self.assertEqual(result["items"][0]["category"], "协同")
        self.assertEqual(result["items"][0]["affair_id"], "sent-123")
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=summary&openFrom=listSent&affairId=sent-123&showTab=true",
        )

    def test_parse_template_list_extracts_template_ids(self):
        html = """
        <div id="section_-6503951670357636432">
          <table class="chessboardtable" title="Seal request">
            <tr><td class="text_overflow hand"
              onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;from=templateNewColl&amp;templateId=-6511139737225050501&amp;showTab=true','obj':this},event)">
              <a>Seal request</a>
            </td></tr>
          </table>
          <table class="chessboardtable" title="Purchase approval">
            <tr><td class="text_overflow hand"
              onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;from=templateNewColl&amp;templateId=3492618929488609812&amp;showTab=true','obj':this},event)">
              <a>Purchase approval</a>
            </td></tr>
          </table>
        </div>
        """

        result = parse_template_list(html, base_url="http://10.10.50.110/seeyon/main.do?method=main")

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"][0]["title"], "Seal request")
        self.assertEqual(result["items"][0]["template_id"], "-6511139737225050501")
        self.assertEqual(result["items"][1]["template_id"], "3492618929488609812")
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
        )

    def test_parse_template_projection_extracts_rows_from_section_api(self):
        projection = {
            "Name": "我的模板",
            "Data": {
                "dataCount": 1,
                "pageNo": 1,
                "rows": [
                    {
                        "cells": [
                            {
                                "cellContentHTML": "<span>Seal request</span>",
                                "id": "template-row-1",
                                "linkURL": "/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
                            }
                        ]
                    }
                ],
            },
        }

        result = parse_template_projection(
            projection,
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
        )

        self.assertEqual(result["source"], "section_api")
        self.assertEqual(result["name"], "我的模板")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Seal request")
        self.assertEqual(result["items"][0]["template_id"], "-6511139737225050501")
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
        )

    def test_parse_template_projection_extracts_chessboard_items_from_section_api(self):
        projection = {
            "Name": "我的模板",
            "Data": {
                "dataNum": 1,
                "pageNo": 0,
                "items": [
                    {
                        "title": "【用印】用印申请单",
                        "name": "【用印】用印申请单",
                        "link": "/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
                        "openType": "4",
                    }
                ],
            },
        }

        result = parse_template_projection(
            projection,
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
        )

        self.assertEqual(result["source"], "section_api")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "【用印】用印申请单")
        self.assertEqual(result["items"][0]["template_id"], "-6511139737225050501")
        self.assertEqual(result["items"][0]["open_type"], "4")
        self.assertEqual(
            result["items"][0]["href"],
            "http://10.10.50.110/seeyon/collaboration/collaboration.do?method=newColl&from=templateNewColl&templateId=-6511139737225050501&showTab=true",
        )

    def test_parse_template_list_accepts_playwright_saved_string_result(self):
        html = json.dumps(
            """
            <div id="section_-6503951670357636432">
              <table class="chessboardtable" title="Seal request">
                <tr><td onclick="javascript:_openDataLink({'url':'/collaboration/collaboration.do?method=newColl&amp;templateId=-6511139737225050501'},event)">
                  <a>Seal request</a>
                </td></tr>
              </table>
            </div>
            """
        )

        result = parse_template_list(html, base_url="http://10.10.50.110/seeyon/main.do?method=main")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["template_id"], "-6511139737225050501")

    def test_parse_navigation_inventory_extracts_portals_shortcuts_and_sections(self):
        html = """
        <ul id="topNav">
          <li id="spaceLi_5834172664846108460" title="个人空间" class="current"
              onclick="javascript:vPortalMainFrameElements.topCenterNav.showNavigation(0,this)">
            <span>个人空间</span>
          </li>
          <li id="spaceLi_3582122093491471500" title="公司空间"
              onclick="javascript:vPortalMainFrameElements.topCenterNav.showNavigation(1,this)">
            <span>公司空间</span>
          </li>
        </ul>
        <ul id="leftNav">
          <li class="lev1Li">
            <div class="lev1Title navTitleName" title="综合查询"
              onclick="javascript:onSeeyonTopNavMenuClick('/seeyon/isearch.do?method=index','-8434000500218049565','mainfrm','F12_isearch',this)">
              <div class="navText">综合查询</div>
            </div>
          </li>
          <li class="lev1Li">
            <div class="lev1Title navTitleName" title="通讯录"
              onclick="javascript:onSeeyonTopNavMenuClick('/seeyon/addressbook.do?method=homeEntry','-567434236207741830','newWindow','F12_addressbook',this)">
              <div class="navText">通讯录</div>
            </div>
          </li>
        </ul>
        <div id="section_556815601453123423">
          <li id="sectionName_-5754227701614689741" title="全部待办"
              onclick="javascript:changeTabAndReloadSection(&quot;556815601453123423&quot;,&quot;-5754227701614689741&quot;)">
            全部待办(5)
          </li>
          <li id="sectionName_1570596005091691914" title="表单审批">表单审批(4)</li>
        </div>
        """

        result = parse_navigation_inventory(
            html,
            base_url="http://10.10.50.110/seeyon/main.do?method=main",
        )

        self.assertEqual(result["portal_count"], 2)
        self.assertEqual(result["portals"][0]["name"], "个人空间")
        self.assertEqual(result["portals"][0]["portal_id"], "5834172664846108460")
        self.assertTrue(result["portals"][0]["active"])
        self.assertEqual(result["portals"][1]["navigation_index"], "1")
        self.assertEqual(result["shortcut_count"], 2)
        self.assertEqual(result["shortcuts"][0]["name"], "综合查询")
        self.assertEqual(result["shortcuts"][0]["menu_id"], "-8434000500218049565")
        self.assertEqual(result["shortcuts"][0]["target"], "mainfrm")
        self.assertEqual(
            result["shortcuts"][0]["href"],
            "http://10.10.50.110/seeyon/isearch.do?method=index",
        )
        self.assertTrue(result["shortcuts"][1]["opens_new_window"])
        self.assertEqual(result["section_count"], 1)
        self.assertEqual(result["sections"][0]["section_id"], "556815601453123423")
        self.assertEqual(result["sections"][0]["tabs"][0]["name"], "全部待办")
        self.assertEqual(result["sections"][0]["tabs"][0]["count"], 5)


if __name__ == "__main__":
    unittest.main()
