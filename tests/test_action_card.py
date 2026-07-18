import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from bscli.auth.action_card import TrustedActionApplication
from bscli.core.write_authorizations import WriteAuthorizationStore


class TrustedActionCardTests(unittest.TestCase):
    def test_card_displays_frozen_business_summary_and_strict_headers(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            authorization = _authorization(store)
            app = TrustedActionApplication(authorization_store=store)

            response = app.get_card(
                authorization["authorization_id"],
                secure_cookie=False,
            )

            html = response.body.decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("保存出差申请草稿", html)
            self.assertIn("济南", html)
            self.assertIn("青岛", html)
            self.assertIn("只保存为待发草稿", html)
            self.assertIn("执行效果", html)
            self.assertIn("授权保存草稿", html)
            self.assertIn(authorization["plan_hash"], html)
            self.assertNotIn("internal-selector", html)
            self.assertIn("HttpOnly", response.headers["Set-Cookie"])
            self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
            self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])
            self.assertIn('postEvent("web_app_ready")', html)
            self.assertIn('postEvent("web_app_expand")', html)
            self.assertIn("if (false && (platform || canPost || canNotify))", html)
            self.assertNotIn("telegram.org", html)

    def test_meeting_card_discloses_immediate_create_and_send_effect(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            authorization = _meeting_authorization(store)
            app = TrustedActionApplication(authorization_store=store)

            response = app.get_card(
                authorization["authorization_id"],
                secure_cookie=False,
            )

            html = response.body.decode("utf-8")
            self.assertIn("创建并发送会议", html)
            self.assertIn("预订会议室并向当前用户发送会议", html)
            self.assertIn("不会仅保存为草稿", html)
            self.assertIn("授权创建并发送", html)
            self.assertNotIn("授权保存草稿", html)

    def test_card_does_not_disable_submitter_before_form_serialization(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            authorization = _authorization(store)
            app = TrustedActionApplication(authorization_store=store)

            response = app.get_card(
                authorization["authorization_id"],
                secure_cookie=False,
            )

            html = response.body.decode("utf-8")
            self.assertIn('name="decision" value="approve"', html)
            self.assertIn('name="decision" value="reject"', html)
            self.assertNotIn("form.addEventListener('submit'", html)

    def test_card_approval_is_csrf_bound_and_single_use(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            authorization = _authorization(store)
            app = TrustedActionApplication(authorization_store=store)
            page = app.get_card(authorization["authorization_id"], secure_cookie=False)
            html = page.body.decode("utf-8")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            cookie = page.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]

            denied = app.submit_card(
                authorization["authorization_id"],
                body=urlencode({"csrf_token": csrf, "decision": "approve"}).encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie="wrong",
            )
            approved = app.submit_card(
                authorization["authorization_id"],
                body=urlencode({"csrf_token": csrf, "decision": "approve"}).encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )
            replay = app.submit_card(
                authorization["authorization_id"],
                body=urlencode({"csrf_token": csrf, "decision": "approve"}).encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )

            self.assertEqual(denied.status, 403)
            self.assertEqual(approved.status, 200)
            self.assertIn("操作已授权", approved.body.decode("utf-8"))
            self.assertIn(
                "if (true && (platform || canPost || canNotify))",
                approved.body.decode("utf-8"),
            )
            self.assertEqual(store.get(authorization["authorization_id"])["state"], "approved")
            self.assertEqual(replay.status, 409)

    def test_rejection_never_creates_an_executable_authorization(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            authorization = _authorization(store)
            app = TrustedActionApplication(authorization_store=store)
            page = app.get_card(authorization["authorization_id"], secure_cookie=True)
            html = page.body.decode("utf-8")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            cookie = page.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]

            response = app.submit_card(
                authorization["authorization_id"],
                body=urlencode({"csrf_token": csrf, "decision": "reject"}).encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=cookie,
            )

            self.assertEqual(response.status, 200)
            self.assertIn("操作已取消", response.body.decode("utf-8"))
            self.assertIn(
                "if (true && (platform || canPost || canNotify))",
                response.body.decode("utf-8"),
            )
            self.assertEqual(store.get(authorization["authorization_id"])["state"], "rejected")
            self.assertIn("Secure", page.headers["Set-Cookie"])


def _authorization(store):
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        capability_name="oa.business_trip.save_draft",
        capability_version="0.1.0",
        prepare_operation_id="prepare-1",
        plan={
            "exact_input": {"reason": "Test"},
            "internal": "internal-selector",
        },
        summary={
            "title": "保存出差申请草稿",
            "principal": "Alice",
            "system": "致远 OA",
            "effect": "仅保存待发草稿",
            "authorization_notice": "授权后只保存为待发草稿，不会发送、提交或进入审批流程。",
            "authorize_label": "授权保存草稿",
            "fields": [
                {"label": "出差始发地", "value": "济南"},
                {"label": "出差目的地", "value": "青岛"},
            ],
        },
        card_base_url="http://127.0.0.1:8780",
    )


def _meeting_authorization(store):
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        capability_name="oa.meeting.create",
        capability_version="0.1.0",
        prepare_operation_id="prepare-meeting-1",
        plan={"business_intent": "create_meeting"},
        summary={
            "title": "创建并发送会议",
            "principal": "Alice",
            "system": "致远 OA",
            "effect": "预订会议室并向当前用户发送会议",
            "authorization_notice": "授权后将立即预订上述会议室并发送会议，不会仅保存为草稿。",
            "authorize_label": "授权创建并发送",
            "fields": [
                {"label": "会议主题", "value": "智能体测试"},
                {"label": "会议室", "value": "4层3#会议室"},
            ],
        },
        card_base_url="http://127.0.0.1:8780",
    )


if __name__ == "__main__":
    unittest.main()
