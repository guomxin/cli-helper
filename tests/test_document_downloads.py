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
