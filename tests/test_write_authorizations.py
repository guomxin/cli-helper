import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.write_authorizations import (
    WriteAuthorizationAccessDenied,
    WriteAuthorizationStateError,
    WriteAuthorizationStore,
)


class WriteAuthorizationStoreTests(unittest.TestCase):
    def test_approved_plan_is_bound_consumed_once_and_immutable(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agentbridge.db"
            store = WriteAuthorizationStore(db_path)
            created = _authorization(store)
            csrf = store.issue_csrf(created["authorization_id"])
            approved = store.decide(
                created["authorization_id"],
                decision="approve",
                csrf_token=csrf,
                csrf_cookie=csrf,
            )

            consumed = store.consume(
                created["authorization_id"],
                user_subject="user-a",
                system_id="oa",
                session_id="session-a",
                capability_name="oa.business_trip.save_draft",
                capability_version="0.1.0",
                commit_operation_id="commit-1",
            )

            self.assertEqual(approved["state"], "approved")
            self.assertEqual(consumed["state"], "consumed")
            self.assertEqual(consumed["plan"]["exact_input"]["reason"], "Test")
            self.assertTrue(consumed["plan_hash"].startswith("sha256:"))
            with self.assertRaisesRegex(WriteAuthorizationStateError, "not approved"):
                store.consume(
                    created["authorization_id"],
                    user_subject="user-a",
                    system_id="oa",
                    session_id="session-a",
                    capability_name="oa.business_trip.save_draft",
                    capability_version="0.1.0",
                    commit_operation_id="commit-2",
                )

            connection = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE write_authorizations SET plan_json = '{}' WHERE authorization_id = ?",
                        (created["authorization_id"],),
                    )
                connection.rollback()
            finally:
                connection.close()

    def test_csrf_user_session_and_capability_bindings_are_enforced(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            created = _authorization(store)
            csrf = store.issue_csrf(created["authorization_id"])

            with self.assertRaises(WriteAuthorizationAccessDenied):
                store.decide(
                    created["authorization_id"],
                    decision="approve",
                    csrf_token=csrf,
                    csrf_cookie="wrong",
                )
            approved = store.decide(
                created["authorization_id"],
                decision="approve",
                csrf_token=csrf,
                csrf_cookie=csrf,
            )
            self.assertEqual(approved["state"], "approved")
            with self.assertRaises(WriteAuthorizationAccessDenied):
                store.consume(
                    created["authorization_id"],
                    user_subject="user-b",
                    system_id="oa",
                    session_id="session-a",
                    capability_name="oa.business_trip.save_draft",
                    capability_version="0.1.0",
                    commit_operation_id="commit-1",
                )
            self.assertEqual(store.get(created["authorization_id"])["state"], "approved")

    def test_new_plan_supersedes_pending_or_approved_plan_and_expiry_is_terminal(self):
        with TemporaryDirectory() as tmp:
            clock = MutableClock(datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc))
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db", clock=clock)
            first = _authorization(store, prepare_operation_id="prepare-1", ttl_seconds=30)
            csrf = store.issue_csrf(first["authorization_id"])
            store.decide(
                first["authorization_id"],
                decision="approve",
                csrf_token=csrf,
                csrf_cookie=csrf,
            )
            second = _authorization(store, prepare_operation_id="prepare-2", ttl_seconds=30)

            self.assertEqual(store.get(first["authorization_id"])["state"], "superseded")
            clock.value += timedelta(seconds=31)
            self.assertEqual(store.get(second["authorization_id"])["state"], "expired")

    def test_card_url_and_identifiers_are_opaque(self):
        with TemporaryDirectory() as tmp:
            store = WriteAuthorizationStore(Path(tmp) / "agentbridge.db")
            created = _authorization(store)

            self.assertRegex(created["authorization_id"], r"^[A-Za-z0-9_-]{32,128}$")
            self.assertTrue(
                created["card_url"].startswith("http://127.0.0.1:8780/authorize/")
            )
            self.assertIsNone(re.search(r"user-a|session-a", created["card_url"]))


def _authorization(
    store,
    *,
    prepare_operation_id="prepare-1",
    ttl_seconds=600,
):
    return store.create(
        user_subject="user-a",
        system_id="oa",
        session_id="session-a",
        capability_name="oa.business_trip.save_draft",
        capability_version="0.1.0",
        prepare_operation_id=prepare_operation_id,
        plan={"exact_input": {"reason": "Test"}},
        summary={"title": "Save draft", "fields": []},
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
