from __future__ import annotations

from datetime import datetime, UTC
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any
from contextlib import contextmanager


class TraceStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def start_run(
        self,
        *,
        system: str,
        command: str,
        args: dict[str, Any],
        access: str,
        strategy: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        now = self._now()
        with self._connection() as conn:
            conn.execute(
                """
                insert into runs (
                    id, system, command, args_json, access, strategy,
                    status, started_at, finished_at, result_json, error
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    system,
                    command,
                    json.dumps(args, ensure_ascii=False),
                    access,
                    strategy,
                    "running",
                    now,
                    None,
                    None,
                    None,
                ),
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result: Any | None = None,
        error: str | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                update runs
                set status = ?, finished_at = ?, result_json = ?, error = ?
                where id = ?
                """,
                (
                    status,
                    self._now(),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return self._decode_row(row)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "select * from runs order by started_at desc"
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                create table if not exists runs (
                    id text primary key,
                    system text not null,
                    command text not null,
                    args_json text not null,
                    access text not null,
                    strategy text not null,
                    status text not null,
                    started_at text not null,
                    finished_at text,
                    result_json text,
                    error text
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _decode_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if data.get("result_json") is not None:
            data["result"] = json.loads(data["result_json"])
        else:
            data["result"] = None
        return data

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
