from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


REQUIRED_RUNTIME_TABLES = {
    "operations",
    "interactions",
    "agent_tasks",
    "runtime_traces",
    "runtime_incidents",
}


def create_runtime_backup(
    db_path: Path | str,
    output_dir: Path | str,
    *,
    release_id: str = "development",
    now: datetime | None = None,
) -> dict[str, Any]:
    source = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError(f"AgentBridge database not found: {source}")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
    backup_path = destination / f"agentbridge-{stamp}.db"
    manifest_path = destination / f"agentbridge-{stamp}.manifest.json"
    if backup_path.exists() or manifest_path.exists():
        raise FileExistsError(f"backup already exists for timestamp {stamp}")

    with closing(sqlite3.connect(source, timeout=30)) as source_connection:
        with closing(sqlite3.connect(backup_path)) as backup_connection:
            source_connection.backup(backup_connection)
    validation = validate_runtime_backup(backup_path)
    if not validation["passed"]:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("created backup did not pass isolated validation")
    manifest = {
        "schemaVersion": "agentbridge.runtime-backup.v1",
        "createdAt": observed_at.isoformat(),
        "releaseId": str(release_id or "development")[:160],
        "databaseFile": backup_path.name,
        "sha256": _sha256(backup_path),
        "byteSize": backup_path.stat().st_size,
        "validation": validation,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "backupPath": str(backup_path), "manifestPath": str(manifest_path)}


def validate_runtime_backup(backup_path: Path | str) -> dict[str, Any]:
    path = Path(backup_path)
    if not path.is_file():
        raise FileNotFoundError(f"runtime backup not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_RUNTIME_TABLES - tables)
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in sorted(REQUIRED_RUNTIME_TABLES & tables)
        }
        isolation_violations = _isolation_violations(connection, tables)
    return {
        "passed": quick_check == "ok" and not missing and not isolation_violations,
        "quickCheck": quick_check,
        "missingTables": missing,
        "rowCounts": counts,
        "isolationViolations": isolation_violations,
        "sha256": _sha256(path),
        "byteSize": path.stat().st_size,
    }


def validate_backup_manifest(manifest_path: Path | str) -> dict[str, Any]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    database_file = str(manifest.get("databaseFile") or "")
    if not database_file or Path(database_file).name != database_file:
        raise ValueError("backup manifest databaseFile is invalid")
    backup_path = path.parent / database_file
    validation = validate_runtime_backup(backup_path)
    expected_hash = str(manifest.get("sha256") or "")
    return {
        **validation,
        "manifestPath": str(path),
        "backupPath": str(backup_path),
        "manifestHashMatches": bool(expected_hash) and expected_hash == validation["sha256"],
        "passed": validation["passed"] and bool(expected_hash) and expected_hash == validation["sha256"],
    }


def _isolation_violations(
    connection: sqlite3.Connection, tables: set[str]
) -> dict[str, int]:
    checks: dict[str, tuple[str, tuple[Any, ...]]] = {}
    if {"runtime_traces", "agent_tasks"} <= tables:
        checks["trace_task_subject"] = (
            """
            SELECT COUNT(*) FROM runtime_traces AS trace
            JOIN agent_tasks AS task ON task.task_id = trace.task_id
            WHERE trace.task_id IS NOT NULL
              AND trace.user_subject <> task.user_subject
            """,
            (),
        )
    if {"runtime_spans", "runtime_traces"} <= tables:
        checks["span_trace_subject"] = (
            """
            SELECT COUNT(*) FROM runtime_spans AS span
            JOIN runtime_traces AS trace ON trace.trace_id = span.trace_id
            WHERE span.user_subject <> trace.user_subject
            """,
            (),
        )
    violations: dict[str, int] = {}
    for name, (query, parameters) in checks.items():
        count = int(connection.execute(query, parameters).fetchone()[0])
        if count:
            violations[name] = count
    return violations


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
