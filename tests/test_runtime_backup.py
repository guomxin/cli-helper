from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bscli.core.runtime_backup import (
    create_runtime_backup,
    validate_backup_manifest,
    validate_runtime_backup,
)


class RuntimeBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "agentbridge.db"
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE operations (operation_id TEXT PRIMARY KEY);
                CREATE TABLE interactions (interaction_id TEXT PRIMARY KEY);
                CREATE TABLE agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_subject TEXT NOT NULL
                );
                CREATE TABLE runtime_traces (
                    trace_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    user_subject TEXT NOT NULL
                );
                CREATE TABLE runtime_spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL
                );
                CREATE TABLE runtime_incidents (incident_id TEXT PRIMARY KEY);
                INSERT INTO agent_tasks VALUES ('task-1', 'user-1');
                INSERT INTO runtime_traces VALUES ('trace-1', 'task-1', 'user-1');
                INSERT INTO runtime_spans VALUES ('span-1', 'trace-1', 'user-1');
                """
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_create_and_validate_consistent_backup(self) -> None:
        report = create_runtime_backup(
            self.db_path,
            self.root / "backups",
            release_id="test-release",
        )
        self.assertTrue(report["validation"]["passed"])
        self.assertEqual(report["releaseId"], "test-release")
        manifest = Path(report["manifestPath"])
        validated = validate_backup_manifest(manifest)
        self.assertTrue(validated["passed"])
        self.assertTrue(validated["manifestHashMatches"])
        self.assertEqual(validated["rowCounts"]["agent_tasks"], 1)

    def test_manifest_hash_detects_tampering(self) -> None:
        report = create_runtime_backup(self.db_path, self.root / "backups")
        manifest_path = Path(report["manifestPath"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        validated = validate_backup_manifest(manifest_path)
        self.assertFalse(validated["passed"])
        self.assertFalse(validated["manifestHashMatches"])

    def test_validation_detects_cross_user_trace_link(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE runtime_traces SET user_subject = 'user-2' WHERE trace_id = 'trace-1'"
            )
            connection.commit()
        validation = validate_runtime_backup(self.db_path)
        self.assertFalse(validation["passed"])
        self.assertEqual(validation["isolationViolations"]["trace_task_subject"], 1)

    def test_validation_detects_missing_runtime_schema(self) -> None:
        incomplete = self.root / "incomplete.db"
        with closing(sqlite3.connect(incomplete)) as connection:
            connection.execute("CREATE TABLE operations (operation_id TEXT)")
            connection.commit()
        validation = validate_runtime_backup(incomplete)
        self.assertFalse(validation["passed"])
        self.assertIn("runtime_traces", validation["missingTables"])


if __name__ == "__main__":
    unittest.main()
