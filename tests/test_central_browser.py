import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.adapters.seeyon_central import SeeyonCentralAdapter, SeeyonLoginRequired
from bscli.browser.central import CentralBrowserWorker, CentralProfileInUseError


class CentralBrowserTests(unittest.TestCase):
    def test_worker_uses_persistent_profile_and_shared_context_request(self):
        with TemporaryDirectory() as tmp:
            controller = FakePlaywrightController()
            worker = CentralBrowserWorker(
                profile_path=Path(tmp) / "profile",
                allowed_origins={"http://oa.example.test"},
                headless=True,
                playwright_starter=lambda: controller,
            )

            with worker:
                response = worker.request("GET", "http://oa.example.test/rest/templates")

            self.assertEqual(response["status"], 200)
            self.assertEqual(response["json"], {"code": 0, "data": {"templates": []}})
            launch = controller.chromium.launches[0]
            self.assertEqual(launch["user_data_dir"], str(Path(tmp) / "profile"))
            self.assertTrue(launch["headless"])
            self.assertEqual(controller.context.request.calls[0]["method"], "GET")
            self.assertEqual(controller.context.request.calls[0]["max_redirects"], 0)
            self.assertTrue(controller.stopped)

    def test_worker_captures_and_restores_allowed_session_cookies(self):
        with TemporaryDirectory() as tmp:
            controller = FakePlaywrightController()
            controller.context.cookie_jar = [
                {
                    "name": "JSESSIONID",
                    "value": "secret",
                    "domain": "oa.example.test",
                    "path": "/",
                }
            ]
            worker = CentralBrowserWorker(
                profile_path=Path(tmp) / "profile",
                allowed_origins={"http://oa.example.test"},
                playwright_starter=lambda: controller,
            )

            with worker:
                state = worker.capture_session_state()
                controller.context.cookie_jar = []
                worker.restore_session_state(state)

            self.assertEqual(controller.context.added_cookies[0][0]["name"], "JSESSIONID")

    def test_worker_rejects_request_outside_allowed_origin(self):
        with TemporaryDirectory() as tmp:
            worker = CentralBrowserWorker(
                profile_path=Path(tmp) / "profile",
                allowed_origins={"http://oa.example.test"},
                playwright_starter=FakePlaywrightController,
            )

            with worker:
                with self.assertRaisesRegex(ValueError, "origin is not allowed"):
                    worker.request("GET", "https://other.example.test/data")

    def test_worker_prevents_concurrent_use_of_the_same_profile(self):
        with TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile"
            first = CentralBrowserWorker(
                profile_path=profile_path,
                allowed_origins={"http://oa.example.test"},
                playwright_starter=FakePlaywrightController,
            )
            second = CentralBrowserWorker(
                profile_path=profile_path,
                allowed_origins={"http://oa.example.test"},
                playwright_starter=FakePlaywrightController,
            )

            with first:
                with self.assertRaises(CentralProfileInUseError):
                    second.start()

            with second:
                self.assertEqual(second.page_title, "OA")

    def test_seeyon_adapter_parses_template_center_response(self):
        worker = FakeWorker(
            {
                "status": 200,
                "url": "http://oa.example.test/seeyon/rest/template/myTemplate",
                "content_type": "application/json",
                "json": {
                    "code": 0,
                    "data": {
                        "templates": [
                            {
                                "id": "tpl-1",
                                "subject": "HR Request",
                                "categoryName": "HR",
                            }
                        ]
                    },
                },
                "text": "",
            }
        )
        adapter = SeeyonCentralAdapter(base_url="http://oa.example.test/seeyon/main.do?method=main")

        result = adapter.list_templates(worker)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["template_id"], "tpl-1")
        self.assertEqual(result["transport"], "central_http_session")

    def test_seeyon_adapter_reports_login_required_for_html_response(self):
        worker = FakeWorker(
            {
                "status": 200,
                "url": "http://oa.example.test/seeyon/login",
                "content_type": "text/html",
                "json": None,
                "text": "<html>login</html>",
            }
        )
        adapter = SeeyonCentralAdapter(base_url="http://oa.example.test/seeyon/main.do?method=main")

        with self.assertRaises(SeeyonLoginRequired):
            adapter.list_templates(worker)


class FakeWorker:
    def __init__(self, response):
        self.response = response

    def request(self, method, url, **_kwargs):
        return {**self.response, "method": method, "requested_url": url}


class FakeResponse:
    status = 200
    url = "http://oa.example.test/rest/templates"

    @property
    def headers(self):
        return {"content-type": "application/json"}

    def text(self):
        return '{"code": 0, "data": {"templates": []}}'

    def json(self):
        return {"code": 0, "data": {"templates": []}}


class FakeRequestContext:
    def __init__(self):
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeResponse()


class FakePage:
    url = "about:blank"

    def goto(self, url, **_kwargs):
        self.url = url

    def title(self):
        return "OA"


class FakeBrowserContext:
    def __init__(self):
        self.request = FakeRequestContext()
        self.pages = [FakePage()]
        self.closed = False
        self.cookie_jar = []
        self.added_cookies = []

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True

    def cookies(self, _urls=None):
        return [dict(cookie) for cookie in self.cookie_jar]

    def add_cookies(self, cookies):
        self.added_cookies.append(cookies)
        self.cookie_jar.extend(dict(cookie) for cookie in cookies)


class FakeChromium:
    def __init__(self, controller):
        self.controller = controller
        self.launches = []

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launches.append({"user_data_dir": user_data_dir, **kwargs})
        return self.controller.context


class FakePlaywrightController:
    def __init__(self):
        self.context = FakeBrowserContext()
        self.chromium = FakeChromium(self)
        self.stopped = False

    def stop(self):
        self.stopped = True


if __name__ == "__main__":
    unittest.main()
