import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.mcp_identities import McpIdentityTokenStore


class McpIdentityTokenStoreTests(unittest.TestCase):
    def test_issue_persists_only_hash_and_verifies_identity(self):
        with TemporaryDirectory() as tmp:
            now = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
            db_path = Path(tmp) / "agentbridge.db"
            store = McpIdentityTokenStore(db_path, clock=lambda: now)

            issued = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                label="desktop-agent",
                ttl_seconds=3600,
            )
            verified = store.verify(issued["token"], required_scopes={"oa:read"})

            self.assertTrue(issued["token"].startswith("abmcp_"))
            self.assertEqual(verified["user_subject"], "user-a")
            self.assertEqual(verified["expected_principal_ref"], "Alice")
            self.assertEqual(verified["scopes"], ["oa:read"])
            self.assertNotIn(issued["token"].encode("utf-8"), db_path.read_bytes())
            self.assertNotIn("token", store.get(issued["token_id"]))

    def test_revoked_and_expired_tokens_fail_verification(self):
        with TemporaryDirectory() as tmp:
            clock = MutableClock(datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc))
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db", clock=clock)
            revoked = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                ttl_seconds=3600,
            )
            expired = store.issue(
                user_subject="user-b",
                expected_principal_ref="Bob",
                ttl_seconds=300,
            )

            record = store.revoke(revoked["token_id"])
            clock.value += timedelta(minutes=6)

            self.assertEqual(record["state"], "revoked")
            self.assertIsNone(store.verify(revoked["token"]))
            self.assertIsNone(store.verify(expired["token"]))

    def test_identity_tokens_are_listed_without_secrets_and_can_be_filtered(self):
        with TemporaryDirectory() as tmp:
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db")
            first = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                label="phone",
            )
            store.issue(
                user_subject="user-b",
                expected_principal_ref="Bob",
                label="desktop",
            )

            records = store.list(user_subject="user-a")

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["token_id"], first["token_id"])
            self.assertNotIn("token", records[0])

    def test_draft_write_scope_is_explicit_and_does_not_replace_read_scope(self):
        with TemporaryDirectory() as tmp:
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db")
            issued = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:draft"],
            )

            verified = store.verify(
                issued["token"],
                required_scopes={"oa:write:draft"},
            )

            self.assertEqual(verified["scopes"], ["oa:read", "oa:write:draft"])
            self.assertIsNotNone(store.verify(issued["token"], required_scopes={"oa:read"}))

    def test_approval_and_meeting_scopes_are_independent(self):
        with TemporaryDirectory() as tmp:
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db")
            approval = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:approval"],
            )
            meeting = store.issue(
                user_subject="user-a",
                expected_principal_ref="Alice",
                scopes=["oa:read", "oa:write:meeting"],
            )

            self.assertIsNotNone(
                store.verify(
                    approval["token"],
                    required_scopes={"oa:write:approval"},
                )
            )
            self.assertIsNone(
                store.verify(
                    approval["token"],
                    required_scopes={"oa:write:meeting"},
                )
            )
            self.assertIsNotNone(
                store.verify(
                    meeting["token"],
                    required_scopes={"oa:write:meeting"},
                )
            )
            self.assertIsNone(
                store.verify(
                    meeting["token"],
                    required_scopes={"oa:write:approval"},
                )
            )
    def test_unsupported_scope_and_unsafe_subject_are_rejected(self):
        with TemporaryDirectory() as tmp:
            store = McpIdentityTokenStore(Path(tmp) / "agentbridge.db")

            with self.assertRaisesRegex(ValueError, "unsupported scope"):
                store.issue(
                    user_subject="user-a",
                    expected_principal_ref="Alice",
                    scopes=["oa:write"],
                )
            with self.assertRaisesRegex(ValueError, "user_subject is invalid"):
                store.issue(
                    user_subject="user\na",
                    expected_principal_ref="Alice",
                )


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


if __name__ == "__main__":
    unittest.main()
