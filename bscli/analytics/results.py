from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from bscli.analytics.contracts import reject
from bscli.core.data_source_secrets import ProtectedJsonStore


class AnalysisResultStore:
    def __init__(self, db_path, root, *, protector=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.payloads = ProtectedJsonStore(root, purpose="agentbridge.analytics-result.v1", protector=protector)
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_results (
                result_id TEXT PRIMARY KEY, owner TEXT NOT NULL, operation_id TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        self.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, *, owner, operation_id, payload):
        result_id = uuid4().hex
        now = datetime.now(timezone.utc)
        # Encrypt first. A failed index insert cannot expose an unowned result.
        self.payloads.save(result_id, payload)
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("INSERT INTO analysis_results VALUES (?,?,?,?,?)",
                             (result_id, owner, operation_id, now.isoformat(), (now + timedelta(days=7)).isoformat()))
        except Exception:
            self.payloads.delete(result_id)
            raise
        return {"result_id": result_id, "protected": True, "schema_version": "taihua.analytics.reference.v1"}

    def load(self, result_id, *, owner):
        if not isinstance(result_id, str) or len(result_id) != 32:
            reject("DATA_ACCESS_DENIED")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM analysis_results WHERE result_id=? AND owner=?", (result_id, owner)).fetchone()
        if row is None:
            reject("DATA_ACCESS_DENIED")
        if row["expires_at"] <= datetime.now(timezone.utc).isoformat():
            self.cleanup()
            reject("RESULT_EXPIRED")
        try:
            return self.payloads.load(result_id)
        except Exception:
            reject("RESULT_ACCESS_REVOKED")

    def cleanup(self):
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("SELECT result_id FROM analysis_results WHERE expires_at<=?",
                                (datetime.now(timezone.utc).isoformat(),)).fetchall()
            for row in rows:
                self.payloads.delete(row["result_id"])
                conn.execute("DELETE FROM analysis_results WHERE result_id=?", (row["result_id"],))
            referenced = {self.payloads.path(row["result_id"]).name
                          for row in conn.execute("SELECT result_id FROM analysis_results")}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
        # A crash between encrypted-file creation and index insertion can leave an orphan.
        for path in self.payloads.root.glob("*.bin"):
            if path.name not in referenced and not path.is_symlink():
                try:
                    if path.stat().st_mtime <= cutoff:
                        path.unlink()
                except FileNotFoundError:
                    pass

    def discard(self, result_id, *, owner):
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT result_id FROM analysis_results WHERE result_id=? AND owner=?", (result_id, owner)).fetchone()
            if row is not None:
                self.payloads.delete(result_id)
                conn.execute("DELETE FROM analysis_results WHERE result_id=?", (result_id,))
