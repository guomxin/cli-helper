from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlparse
import unittest

from bscli.adapters.smartlight import (
    SMARTLIGHT_ALARM_LIST_CAPABILITY,
    SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
    SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
    SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
    SMARTLIGHT_OVERVIEW_CAPABILITY,
    SmartlightAuthenticationRejected,
    SmartlightCentralAdapter,
    build_smartlight_capability_registry,
)


class SmartlightAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = SmartlightCentralAdapter(
            base_url="http://123.232.113.241:4101/smartlight",
            allow_insecure_http=True,
        )

    def test_plain_http_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            SmartlightCentralAdapter(
                base_url="http://123.232.113.241:4101/smartlight"
            )

    def test_authentication_contract_uses_trusted_captcha_card(self):
        contract = self.adapter.authentication_contract()

        self.assertEqual(contract["system_id"], "smartlight")
        self.assertNotIn("authentication_mode", contract)
        self.assertEqual(
            [field["name"] for field in contract["fields"]],
            ["username", "password", "authcode"],
        )
        self.assertEqual(
            contract["prepared_authentication"]["kind"],
            "image_captcha",
        )

    def test_prepares_captcha_then_authenticates_and_captures_jwt(self):
        worker = FakeSmartlightWorker()

        prepared = self.adapter.prepare_authentication(worker, timeout_seconds=20)
        recovered = self.adapter.recover_prepared_authentication(worker)
        result = self.adapter.authenticate(
            worker,
            {"username": "yanshi", "password": "secret", "authcode": "1234"},
            timeout_seconds=20,
        )

        self.assertEqual(prepared["captcha"]["content_type"], "image/jpeg")
        self.assertEqual(recovered, prepared)
        self.assertEqual(result["observed_principal_ref"], "无为")
        self.assertEqual(result["principal"]["account"], "yanshi")
        self.assertNotIn("password_digest", result["principal"])
        self.assertEqual(worker.state["access_token"], "jwt-access")
        submitted = parse_qs(worker.cas_submission)
        self.assertEqual(submitted["username"], ["eWFuc2hp"])
        self.assertEqual(submitted["authcode"], ["1234"])
        self.assertNotEqual(submitted["password"], ["secret"])
        self.assertEqual(worker.cas_headers["Origin"], self.adapter.origin)
        self.assertIn("/cas/login", worker.cas_headers["Referer"])
        self.assertIn("Mozilla/5.0", worker.cas_headers["User-Agent"])
        self.assertIn("/cas/login", worker.captcha_headers["Referer"])

    def test_classifies_captcha_rejection_from_cas_error_page(self):
        worker = FakeSmartlightWorker(login_rejection="验证码错误，请重新输入")
        self.adapter.prepare_authentication(worker, timeout_seconds=20)

        with self.assertRaises(SmartlightAuthenticationRejected) as raised:
            self.adapter.authenticate(
                worker,
                {"username": "yanshi", "password": "secret", "authcode": "1234"},
                timeout_seconds=20,
            )

        self.assertEqual(raised.exception.error_code, "CAPTCHA_REJECTED")

    def test_classifies_account_rejection_from_cas_error_page(self):
        worker = FakeSmartlightWorker(login_rejection="用户名或密码错误")
        self.adapter.prepare_authentication(worker, timeout_seconds=20)

        with self.assertRaises(SmartlightAuthenticationRejected) as raised:
            self.adapter.authenticate(
                worker,
                {"username": "yanshi", "password": "secret", "authcode": "1234"},
                timeout_seconds=20,
            )

        self.assertEqual(raised.exception.error_code, "CREDENTIALS_REJECTED")

    def test_read_capabilities_use_observed_api_contracts(self):
        worker = FakeSmartlightWorker(authenticated=True)

        overview = self.adapter.invoke_capability(
            SMARTLIGHT_OVERVIEW_CAPABILITY, worker, {}
        )
        lamp_posts = self.adapter.invoke_capability(
            SMARTLIGHT_LAMPPOST_LIST_CAPABILITY,
            worker,
            {"keyword": "LP-", "page": 1, "size": 10},
        )
        alarms = self.adapter.invoke_capability(
            SMARTLIGHT_ALARM_LIST_CAPABILITY,
            worker,
            {"page": 1, "size": 10},
        )
        tasks = self.adapter.invoke_capability(
            SMARTLIGHT_INSPECTION_TASK_LIST_CAPABILITY,
            worker,
            {"task_name": "夜巡", "page": 1, "size": 10},
        )
        leakage = self.adapter.invoke_capability(
            SMARTLIGHT_LEAKAGE_SUMMARY_CAPABILITY,
            worker,
            {"start_date": "2026-08-01", "end_date": "2026-08-11"},
        )

        self.assertEqual(overview["cabinetTotal"], 1)
        self.assertEqual(overview["lampPostTotal"], 116)
        self.assertEqual(lamp_posts["items"][0]["code"], "LP-001")
        self.assertEqual(alarms["summary"]["untreated"], 2)
        self.assertEqual(tasks["items"][0]["taskName"], "夜巡一组")
        self.assertEqual(leakage["summary"]["alarmCount"], 1)
        self.assertTrue(
            all("x-Authentication-Token" in headers for headers in worker.api_headers)
        )

    def test_registry_contains_five_read_only_capabilities(self):
        capabilities = build_smartlight_capability_registry().list()

        self.assertEqual(len(capabilities), 5)
        self.assertTrue(all(spec.effect == "read" for spec in capabilities))


class FakePage:
    def content(self) -> str:
        return """
        <form id="auth-form" action="/cas/login;jsessionid=test?service=x">
          <input type="hidden" name="lt" value="LT-test">
          <input type="hidden" name="execution" value="exec-test">
          <input type="hidden" name="_eventId" value="submit">
          <img id="captchaImage" src="captcha.jpg">
        </form>
        """


class FakeSmartlightWorker:
    def __init__(
        self,
        *,
        authenticated: bool = False,
        login_rejection: str | None = None,
    ) -> None:
        self.state = {}
        self.page_url = ""
        self.cas_submission = ""
        self.cas_headers: dict = {}
        self.captcha_headers: dict = {}
        self.api_headers: list[dict] = []
        self.login_rejection = login_rejection
        self.login_completed = authenticated
        if authenticated:
            self.state = {
                "access_token": "jwt-access",
                "refresh_token": "jwt-refresh",
                "principal": _safe_principal(),
            }

    def goto(self, url: str, *, timeout_seconds: float = 30):
        del url, timeout_seconds
        self.page_url = "http://123.232.113.241:4101/cas/login?service=test"
        return FakePage()

    def request_bytes(self, method: str, url: str, **kwargs) -> dict:
        path = urlparse(url).path
        if method == "GET" and path == "/smartlight/" and not self.login_completed:
            return {
                "status": 302,
                "url": url,
                "content_type": "text/html",
                "body": b"",
                "location": "/cas/login?service=http%3A%2F%2Ftest%2Fsmartlight%2F",
            }
        if method == "GET" and path == "/cas/login":
            return {
                "status": 200,
                "url": url,
                "content_type": "text/html",
                "body": FakePage().content().encode("utf-8"),
                "location": None,
            }
        if method == "GET" and path.endswith("/cas/captcha.jpg"):
            self.captcha_headers = dict(kwargs.get("headers") or {})
            return {
                "status": 200,
                "url": url,
                "content_type": "image/jpeg",
                "body": b"jpeg-captcha",
                "location": None,
            }
        if method == "POST" and path.startswith("/cas/login"):
            self.cas_submission = kwargs["body"]
            self.cas_headers = dict(kwargs.get("headers") or {})
            if self.login_rejection is not None:
                body = f"""
                <form id="auth-form">
                  <div class="controls login-error-info">
                    {self.login_rejection}
                  </div>
                </form>
                """.encode("utf-8")
                return {
                    "status": 200,
                    "url": url,
                    "content_type": "text/html; charset=UTF-8",
                    "body": body,
                    "location": None,
                }
            self.login_completed = True
            return {
                "status": 302,
                "url": url,
                "content_type": "text/html",
                "body": b"",
                "location": "/smartlight/?ticket=ST-test",
            }
        if method == "GET" and path == "/smartlight/":
            return {
                "status": 200,
                "url": url,
                "content_type": "text/html",
                "body": b"app",
                "location": None,
            }
        raise AssertionError(f"unexpected bytes request: {method} {url}")

    def request(self, method: str, url: str, **kwargs) -> dict:
        self.assert_post(method)
        path = urlparse(url).path
        if path == "/smartlight/userInfo/getCasLoginUser":
            return _response(
                {
                    "dlzh": "yanshi",
                    "userName": "无为",
                    "dlmm": "D" * 96,
                    "organId": "org-1",
                    "organroleId": "role-1",
                    "organroleName": "wuwei",
                    "ryid": "person-1",
                    "yhid": "user-1",
                }
            )
        if path == "/jwtcenter//JWTInfoController/getToken":
            return _response(
                {
                    "resp_code": 0,
                    "resp_data": {
                        "access_token": "jwt-access",
                        "refresh_token": "jwt-refresh",
                        "access_token_duration": 3600,
                    },
                }
            )
        self.api_headers.append(dict(kwargs.get("headers") or {}))
        if path.endswith("/map/getIntegratedRControlCabinetDataByCondition"):
            return _response(
                [
                    {
                        "controlCabinetId": "cab-1",
                        "controlCabinetCode": "CAB-001",
                        "controlCabinetName": "一号箱变",
                        "onlineState": "online",
                    }
                ]
            )
        if path.endswith("/lLamppost/getLampDetialList"):
            return _response({"list": [], "totalCount": 116})
        if path.endswith("/lLamppost/getDataByConditionForFacilityEx"):
            return _response(
                {
                    "list": [
                        {
                            "lampPostID": "lp-1",
                            "LampPostCode": "LP-001",
                            "StreetName": "测试路",
                        }
                    ],
                    "totalCount": 1,
                }
            )
        if path.endswith("/rHisHitchAlarm/getDataByRtuAlarm"):
            return _response(
                {
                    "RtuHisHitchAlarm": {
                        "list": [{"alarmId": "alarm-1", "alarmContent": "掉线"}],
                        "totalCount": 1,
                    },
                    "todayAlarm": 3,
                    "untreated": 2,
                    "yesterdayAlarm": 1,
                }
            )
        if path.endswith("/inspectionTask/getDataByCondition"):
            return _response(
                {
                    "list": [{"taskId": "task-1", "taskName": "夜巡一组"}],
                    "totalCount": 1,
                }
            )
        if path.endswith("/lHisCoplog/getDataByLampElectricLeakage"):
            return _response(
                {"list": [{"id": "leak-1", "electricLeakage": 12}], "totalCount": 1}
            )
        if path.endswith("/lHisCoplog/queryLampElectricLeakageCount"):
            return _response({"alarmCount": 1, "deviceCount": 1})
        raise AssertionError(f"unexpected API request: {method} {url}")

    def get_http_state(self) -> dict:
        return deepcopy(self.state)

    def set_http_state(self, value: dict) -> None:
        self.state = deepcopy(value)

    @staticmethod
    def assert_post(method: str) -> None:
        if method != "POST":
            raise AssertionError(f"unexpected method: {method}")


def _safe_principal() -> dict:
    return {
        "account": "yanshi",
        "name": "无为",
        "organId": "org-1",
        "organroleId": "role-1",
        "organroleName": "wuwei",
        "personId": "person-1",
        "userId": "user-1",
    }


def _response(payload) -> dict:
    return {
        "status": 200,
        "url": "http://123.232.113.241:4101/smartlight/api",
        "content_type": "application/json",
        "json": payload,
        "text": "",
        "elapsed_ms": 1,
    }


if __name__ == "__main__":
    unittest.main()
