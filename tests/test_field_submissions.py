import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.field_submissions import (
    FieldSubmissionAccessDenied,
    FieldSubmissionStateError,
    FieldSubmissionStore,
)


class FieldSubmissionStoreTests(unittest.TestCase):
    def test_values_are_csrf_bound_consumed_once_and_immutable(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agentbridge.db"
            store = FieldSubmissionStore(db_path)
            created = _submission(store)
            csrf = store.issue_csrf(created["submission_id"])

            submitted = store.submit(
                created["submission_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
                values={"reason": "客户交流"},
            )
            consumed = store.consume(
                created["submission_id"],
                user_subject="user-a",
                system_id="oa",
                session_id="session-a",
                capability_name="oa.business_trip.prepare",
                capability_version="0.2.0",
                consume_operation_id="prepare-2",
            )

            self.assertEqual(submitted["state"], "submitted")
            self.assertNotIn("values", submitted)
            self.assertEqual(consumed["state"], "consumed")
            self.assertEqual(consumed["values"], {"reason": "客户交流"})
            with self.assertRaisesRegex(FieldSubmissionStateError, "not submitted"):
                store.consume(
                    created["submission_id"],
                    user_subject="user-a",
                    system_id="oa",
                    session_id="session-a",
                    capability_name="oa.business_trip.prepare",
                    capability_version="0.2.0",
                    consume_operation_id="prepare-3",
                )

            connection = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE field_submissions SET values_json = '{}' WHERE submission_id = ?",
                        (created["submission_id"],),
                    )
                connection.rollback()
            finally:
                connection.close()

    def test_csrf_and_user_session_capability_bindings_are_enforced(self):
        with TemporaryDirectory() as tmp:
            store = FieldSubmissionStore(Path(tmp) / "agentbridge.db")
            created = _submission(store)
            csrf = store.issue_csrf(created["submission_id"])

            with self.assertRaises(FieldSubmissionAccessDenied):
                store.submit(
                    created["submission_id"],
                    csrf_token=csrf,
                    csrf_cookie="wrong",
                    values={"reason": "Test"},
                )
            store.submit(
                created["submission_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
                values={"reason": "Test"},
            )
            with self.assertRaises(FieldSubmissionAccessDenied):
                store.consume(
                    created["submission_id"],
                    user_subject="user-b",
                    system_id="oa",
                    session_id="session-a",
                    capability_name="oa.business_trip.prepare",
                    capability_version="0.2.0",
                    consume_operation_id="prepare-2",
                )
            self.assertEqual(store.get(created["submission_id"])["state"], "submitted")

    def test_new_card_supersedes_pending_or_submitted_card_and_expiry_is_terminal(self):
        with TemporaryDirectory() as tmp:
            clock = MutableClock(datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc))
            store = FieldSubmissionStore(Path(tmp) / "agentbridge.db", clock=clock)
            first = _submission(store, operation_id="prepare-1", ttl_seconds=30)
            csrf = store.issue_csrf(first["submission_id"])
            store.submit(
                first["submission_id"],
                csrf_token=csrf,
                csrf_cookie=csrf,
                values={"reason": "Test"},
            )
            second = _submission(store, operation_id="prepare-2", ttl_seconds=30)

            self.assertEqual(store.get(first["submission_id"])["state"], "superseded")
            clock.value += timedelta(seconds=31)
            self.assertEqual(store.get(second["submission_id"])["state"], "expired")

    def test_card_url_and_identifier_are_opaque(self):
        with TemporaryDirectory() as tmp:
            store = FieldSubmissionStore(Path(tmp) / "agentbridge.db")
            created = _submission(store)

            self.assertRegex(created["submission_id"], r"^[A-Za-z0-9_-]{32,128}$")
            self.assertTrue(created["card_url"].startswith("http://127.0.0.1:8780/input/"))
            self.assertIsNone(re.search(r"user-a|session-a", created["card_url"]))

    def test_endpoint_presentations_have_independent_sessions_and_one_winner(self):
        with TemporaryDirectory() as tmp:
            store = FieldSubmissionStore(Path(tmp) / "agentbridge.db")
            created = _submission(store)
            telegram = store.create_presentation(
                created["submission_id"],
                user_subject="user-a",
                endpoint_id="endpoint-telegram",
            )
            workspace = store.create_presentation(
                created["submission_id"],
                user_subject="user-a",
                endpoint_id="endpoint-workspace",
            )
            telegram_csrf = store.issue_csrf(
                created["submission_id"],
                presentation_id=telegram["presentation_id"],
            )
            workspace_csrf = store.issue_csrf(
                created["submission_id"],
                presentation_id=workspace["presentation_id"],
            )

            self.assertNotEqual(telegram["card_url"], workspace["card_url"])
            self.assertNotEqual(telegram_csrf, workspace_csrf)
            submitted = store.submit(
                created["submission_id"],
                csrf_token=telegram_csrf,
                csrf_cookie=telegram_csrf,
                values={"reason": "Test"},
                presentation_id=telegram["presentation_id"],
            )
            self.assertEqual(submitted["state"], "submitted")
            with self.assertRaises(FieldSubmissionStateError):
                store.submit(
                    created["submission_id"],
                    csrf_token=workspace_csrf,
                    csrf_cookie=workspace_csrf,
                    values={"reason": "Duplicate"},
                    presentation_id=workspace["presentation_id"],
                )
            self.assertEqual(
                store.get_presentation(
                    created["submission_id"],
                    workspace["presentation_id"],
                )["state"],
                "submitted",
            )

    def test_presentation_is_bound_to_the_same_user(self):
        with TemporaryDirectory() as tmp:
            store = FieldSubmissionStore(Path(tmp) / "agentbridge.db")
            created = _submission(store)
            with self.assertRaises(KeyError):
                store.create_presentation(
                    created["submission_id"],
                    user_subject="user-b",
                    endpoint_id="endpoint-other",
                )


def _submission(store, *, operation_id="prepare-1", ttl_seconds=900):
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        capability_name="oa.business_trip.prepare",
        capability_version="0.2.0",
        create_operation_id=operation_id,
        form_schema={
            "schema_version": "test.fields.v1",
            "title": "填写字段",
            "fields": [
                {
                    "name": "reason",
                    "label": "事由",
                    "control": "text",
                    "required": True,
                    "max_length": 100,
                }
            ],
        },
        card_base_url="http://127.0.0.1:8780",
        ttl_seconds=ttl_seconds,
    )


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


if __name__ == "__main__":
    unittest.main()
