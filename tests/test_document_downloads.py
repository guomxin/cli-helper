from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.auth.document_download import TrustedDocumentDownloadApplication
from bscli.core.central_service import CentralCapabilityService
from bscli.core.document_downloads import (
    DocumentDownloadAccessDenied,
    DocumentDownloadStateError,
    DocumentDownloadStore,
)


class DocumentDownloadStoreTests(unittest.TestCase):
    def test_download_is_bound_to_immutable_reference_and_consumed_once(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = store.create(
                user_subject="user-a",
                system_id="oa",
                session_id="session-a",
                document=_reference(),
                filename="certificate.pdf",
                document_type="patent_certificate",
                display_size="1.2 MB",
                card_base_url="https://10.10.50.213:8780",
            )

            token = store.issue_csrf(created["download_id"])
            with self.assertRaises(DocumentDownloadAccessDenied):
                store.claim(
                    created["download_id"],
                    csrf_token=token,
                    csrf_cookie="different",
                )

            claimed = store.claim(
                created["download_id"],
                csrf_token=token,
                csrf_cookie=token,
            )
            self.assertEqual(claimed["document"]["source_id"], "file-1")
            self.assertEqual(store.complete(created["download_id"])["state"], "completed")
            with self.assertRaises(DocumentDownloadStateError):
                store.issue_csrf(created["download_id"])

    def test_failed_fetch_can_be_released_for_a_new_csrf_bound_retry(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = _create(store)
            token = store.issue_csrf(created["download_id"])
            store.claim(
                created["download_id"],
                csrf_token=token,
                csrf_cookie=token,
            )

            released = store.release(created["download_id"])

            self.assertEqual(released["state"], "pending")
            self.assertNotEqual(store.issue_csrf(created["download_id"]), token)

    def test_machine_prepare_is_user_bound_cached_and_reusable(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = _create(store)

            with self.assertRaises(DocumentDownloadAccessDenied):
                store.claim_for_prepare(
                    created["download_id"],
                    user_subject="user-b",
                )

            store.claim_for_prepare(
                created["download_id"],
                user_subject="user-a",
            )
            ready = store.mark_ready(
                created["download_id"],
                body=b"%PDF-1.7\nprepared",
                content_type="application/pdf",
            )
            first = store.ready_payload(
                created["download_id"],
                user_subject="user-a",
            )
            second = store.ready_payload(
                created["download_id"],
                user_subject="user-a",
            )

            self.assertEqual(ready["state"], "ready")
            self.assertEqual(first["body"], second["body"])
            self.assertEqual(first["prepared_size"], len(first["body"]))

    def test_prepared_file_gets_a_fresh_thirty_minute_delivery_window(self):
        now = [datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)]
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(
                Path(tmp) / "agentbridge.db",
                clock=lambda: now[0],
            )
            created = _create(store, ttl_seconds=60)
            now[0] += timedelta(seconds=30)
            store.claim_for_prepare(
                created["download_id"],
                user_subject="user-a",
            )
            ready = store.mark_ready(
                created["download_id"],
                body=b"%PDF-1.7\nprepared",
                content_type="application/pdf",
            )

            self.assertEqual(
                datetime.fromisoformat(ready["expires_at"]),
                now[0] + timedelta(minutes=30),
            )
            now[0] += timedelta(minutes=29)
            self.assertEqual(store.get(created["download_id"])["state"], "ready")
            now[0] += timedelta(minutes=2)
            self.assertEqual(store.get(created["download_id"])["state"], "expired")

    def test_expired_download_cannot_be_claimed(self):
        now = [datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)]
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(
                Path(tmp) / "agentbridge.db",
                clock=lambda: now[0],
            )
            created = _create(store, ttl_seconds=60)
            token = store.issue_csrf(created["download_id"])
            now[0] += timedelta(seconds=61)

            with self.assertRaises(DocumentDownloadStateError):
                store.claim(
                    created["download_id"],
                    csrf_token=token,
                    csrf_cookie=token,
                )
            self.assertEqual(store.get(created["download_id"])["state"], "expired")


class TrustedDocumentDownloadApplicationTests(unittest.TestCase):
    def test_card_fetches_pdf_only_after_csrf_bound_confirmation(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = _create(store)
            application = TrustedDocumentDownloadApplication(
                download_store=store,
                fetcher=lambda record: {
                    "body": b"%PDF-1.7\ncertificate",
                    "filename": record["filename"],
                    "content_type": "application/pdf",
                },
            )

            card = application.get_card(created["download_id"], secure_cookie=True)
            html = card.body.decode("utf-8")
            csrf_token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                html,
            ).group(1)
            self.assertIn("Secure", card.headers["Set-Cookie"])
            self.assertNotIn("file-1", html)

            response = application.submit_card(
                created["download_id"],
                body=f"csrf_token={csrf_token}".encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=csrf_token,
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/pdf")
            self.assertTrue(response.body.startswith(b"%PDF-"))
            self.assertEqual(store.get(created["download_id"])["state"], "completed")

    def test_card_returns_jpeg_scan_with_safe_download_filename(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = store.create(
                user_subject="user-a",
                system_id="oa",
                session_id="session-a",
                document={**_reference(), "filename": "旧软著V1.0.jpg"},
                filename="旧软著V1.0.jpg",
                document_type="software_copyright_certificate",
                display_size="1.2 MB",
                card_base_url="https://10.10.50.213:8780",
            )
            application = TrustedDocumentDownloadApplication(
                download_store=store,
                fetcher=lambda record: {
                    "body": b"\xff\xd8\xff\xe0scan",
                    "filename": record["filename"],
                    "content_type": "image/jpeg",
                },
            )

            card = application.get_card(created["download_id"], secure_cookie=True)
            html = card.body.decode("utf-8")
            self.assertIn("下载证书扫描件", html)
            csrf_token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                html,
            ).group(1)
            response = application.submit_card(
                created["download_id"],
                body=f"csrf_token={csrf_token}".encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=csrf_token,
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            self.assertIn("filename=certificate.jpg", response.headers["Content-Disposition"])
            self.assertTrue(response.body.startswith(b"\xff\xd8\xff"))

    def test_ready_file_is_served_without_refetching_oa(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = _create(store)
            store.claim_for_prepare(created["download_id"], user_subject="user-a")
            store.mark_ready(
                created["download_id"],
                body=b"%PDF-1.7\nprepared",
                content_type="application/pdf",
            )
            application = TrustedDocumentDownloadApplication(
                download_store=store,
                fetcher=lambda _record: self.fail("ready file must not refetch OA"),
            )

            response = application.get_file(created["download_id"])
            card_response = application.get_card(
                created["download_id"],
                secure_cookie=True,
            )

            self.assertEqual(response.status, 200)
            self.assertEqual(card_response.body, response.body)
            self.assertEqual(response.headers["Content-Type"], "application/pdf")

    def test_fetch_failure_returns_retryable_card_state(self):
        with TemporaryDirectory() as tmp:
            store = DocumentDownloadStore(Path(tmp) / "agentbridge.db")
            created = _create(store)

            def fail(_record):
                raise RuntimeError("upstream unavailable")

            application = TrustedDocumentDownloadApplication(
                download_store=store,
                fetcher=fail,
            )
            card = application.get_card(created["download_id"], secure_cookie=False)
            csrf_token = re.search(
                rb'name="csrf_token" value="([^"]+)"',
                card.body,
            ).group(1).decode()

            response = application.submit_card(
                created["download_id"],
                body=f"csrf_token={csrf_token}".encode(),
                content_type="application/x-www-form-urlencoded",
                csrf_cookie=csrf_token,
            )

            self.assertEqual(response.status, 502)
            self.assertEqual(store.get(created["download_id"])["state"], "pending")


class CentralDocumentDownloadBindingTests(unittest.TestCase):
    def test_search_result_exposes_only_short_lived_url_not_oa_ids(self):
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://oa.example.test/seeyon/main.do",
                trusted_card_base_url="https://10.10.50.213:8780",
            )
            result = service._materialize_document_downloads(
                session={
                    "user_subject": "user-a",
                    "system_id": "oa",
                    "session_id": "session-a",
                },
                result={
                    "count": 1,
                    "items": [
                        {
                            "title": "Certificate",
                            "filename": "certificate.pdf",
                            "document_type": "patent_certificate",
                            "display_size": "1.2 MB",
                            "match_kind": "exact",
                            "_download_reference": _reference(),
                        }
                    ],
                },
            )

            item = result["items"][0]
            self.assertNotIn("_download_reference", item)
            self.assertNotIn("source_id", item)
            self.assertTrue(
                item["download_url"].startswith(
                    "https://10.10.50.213:8780/download/"
                )
            )
            self.assertIn("download_expires_at", item)
            self.assertRegex(item["download_id"], r"^[A-Za-z0-9_-]{32,128}$")

    def test_prepare_download_fetches_once_and_returns_fast_media_url(self):
        with TemporaryDirectory() as tmp:
            service = CentralCapabilityService(
                home=tmp,
                base_url="http://oa.example.test/seeyon/main.do",
                trusted_card_base_url="https://10.10.50.213:8780",
            )
            grant = service.document_downloads.create(
                user_subject="user-a",
                system_id="oa",
                session_id="session-a",
                document=_reference(),
                filename="certificate.pdf",
                document_type="patent_certificate",
                display_size="1.2 MB",
                card_base_url="https://10.10.50.213:8780",
            )
            calls = []

            def fetch(record):
                calls.append(record["download_id"])
                return {
                    "body": b"%PDF-1.7\nprepared",
                    "filename": record["filename"],
                    "content_type": "application/pdf",
                }

            service.fetch_document_download = fetch
            endpoint, _ = service.tasks.ensure_endpoint(
                user_subject="user-a",
                token_id="token-a",
                agent_host="openclaw",
                endpoint_key="telegram:*:1001",
                client_type="telegram",
                external_subject="1001",
                conversation_ref="agent:main:telegram:direct:1001",
            )
            task, _ = service.tasks.ensure_task(
                user_subject="user-a",
                agent_host="openclaw",
                host_task_key="session|certificate-download",
                origin_endpoint_id=endpoint["endpoint_id"],
                active_conversation_ref=endpoint["conversation_ref"],
                title="Download OA certificate",
            )
            first = service.prepare_document_download(
                user_subject="user-a",
                download_id=grant["download_id"],
                task_id=task["task_id"],
            )
            second = service.prepare_document_download(
                user_subject="user-a",
                download_id=grant["download_id"],
                task_id=task["task_id"],
            )
            denied = service.prepare_document_download(
                user_subject="user-b",
                download_id=grant["download_id"],
            )

            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(second["file"]["mediaUrl"], first["file"]["mediaUrl"])
            self.assertEqual(
                second["file"]["artifactId"],
                first["file"]["artifactId"],
            )
            self.assertFalse(first["file"]["artifactReused"])
            self.assertTrue(second["file"]["artifactReused"])
            self.assertEqual(calls, [grant["download_id"]])
            self.assertTrue(first["file"]["mediaUrl"].endswith("/file"))
            self.assertEqual(
                service.tasks.list_artifacts(
                    task_id=task["task_id"],
                    user_subject="user-a",
                )[0]["filename"],
                "certificate.pdf",
            )
            completed_task = service.tasks.get_task(
                task["task_id"],
                user_subject="user-a",
            )
            self.assertEqual(completed_task["status"], "succeeded")
            self.assertEqual(
                [
                    event["event_type"]
                    for event in service.tasks.list_events(
                        task_id=task["task_id"],
                        user_subject="user-a",
                    )
                    if event["event_type"] == "task.completed"
                ],
                ["task.completed"],
            )
            self.assertEqual(denied["error"]["code"], "DOWNLOAD_ACCESS_DENIED")

def _reference() -> dict:
    return {
        "resource_id": "resource-1",
        "source_id": "file-1",
        "filename": "certificate.pdf",
        "display_size": "1.2 MB",
        "document_type": "patent_certificate",
        "category_label": "1-\u4e13\u5229\u8bc1\u4e66\u626b\u63cf\u4ef6",
        "create_date": "2026-07-27",
        "version": "v1",
        "mime_type_id": "22",
        "secret_level": "1",
        "is_upload_file": True,
    }


def _create(store: DocumentDownloadStore, *, ttl_seconds: int = 600) -> dict:
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        document=_reference(),
        filename="certificate.pdf",
        document_type="patent_certificate",
        display_size="1.2 MB",
        card_base_url="https://10.10.50.213:8780",
        ttl_seconds=ttl_seconds,
    )


if __name__ == "__main__":
    unittest.main()
