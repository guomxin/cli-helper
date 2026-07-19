from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Callable, Iterator
from uuid import uuid4


_ALLOWED_SCOPES = frozenset(
    {
        "oa:read",
        "oa:write:draft",
        "oa:write:approval",
        "oa:write:meeting",
        "oa:write:submit",
    }
)


class McpIdentityTokenStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_identity_tokens (
                    token_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_subject TEXT NOT NULL,
                    expected_principal_ref TEXT NOT NULL,
                    label TEXT,
                    scopes_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS mcp_identity_tokens_subject_state
                ON mcp_identity_tokens (user_subject, state, created_at)
                """
            )

    def issue(
        self,
        *,
        user_subject: str,
        expected_principal_ref: str,
        label: str | None = None,
        scopes: list[str] | None = None,
        ttl_seconds: int = 86400,
    ) -> dict:
        user_subject = _validate_user_subject(user_subject)
        expected_principal_ref = _validate_expected_principal(expected_principal_ref)
        normalized_label = _validate_label(label)
        normalized_scopes = _validate_scopes(scopes or ["oa:read"])
        if ttl_seconds < 300 or ttl_seconds > 90 * 86400:
            raise ValueError("MCP identity token TTL must be between 5 minutes and 90 days")

        now = _as_utc(self.clock())
        token_id = str(uuid4())
        secret = f"abmcp_{secrets.token_urlsafe(32)}"
        token_hash = _token_hash(secret)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_identity_tokens (
                    token_id, token_hash, user_subject, expected_principal_ref,
                    label, scopes_json, state, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    token_id,
                    token_hash,
                    user_subject,
                    expected_principal_ref,
                    normalized_label,
                    json.dumps(normalized_scopes, ensure_ascii=True, separators=(",", ":")),
                    _format_time(now),
                    _format_time(expires_at),
                ),
            )
        return {**self.get(token_id), "token": secret}

    def verify(self, token: str, *, required_scopes: set[str] | None = None) -> dict | None:
        if not isinstance(token, str) or not token.startswith("abmcp_") or len(token) > 256:
            return None
        now = _as_utc(self.clock())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_identity_tokens WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            record = _record_from_row(row)
            if record["state"] != "active" or _parse_time(record["expires_at"]) <= now:
                return None
            if required_scopes and not required_scopes.issubset(set(record["scopes"])):
                return None
            connection.execute(
                "UPDATE mcp_identity_tokens SET last_used_at = ? WHERE token_id = ?",
                (_format_time(now), record["token_id"]),
            )
        record["last_used_at"] = _format_time(now)
        return record

    def resolve_client(self, token_id: str, *, required_scopes: set[str] | None = None) -> dict:
        record = self.get(token_id)
        now = _as_utc(self.clock())
        if record["state"] != "active" or _parse_time(record["expires_at"]) <= now:
            raise PermissionError("MCP identity token is inactive or expired")
        if required_scopes and not required_scopes.issubset(set(record["scopes"])):
            raise PermissionError("MCP identity token does not grant the required scope")
        return record

    def get(self, token_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_identity_tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"MCP identity token not found: {token_id}")
        return _record_from_row(row)

    def list(self, *, user_subject: str | None = None, limit: int = 100) -> list[dict]:
        if limit < 1 or limit > 1000:
            raise ValueError("MCP identity token list limit must be between 1 and 1000")
        query = "SELECT * FROM mcp_identity_tokens"
        parameters: list[object] = []
        if user_subject is not None:
            query += " WHERE user_subject = ?"
            parameters.append(_validate_user_subject(user_subject))
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_record_from_row(row) for row in rows]

    def revoke(self, token_id: str) -> dict:
        now = _as_utc(self.clock())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE mcp_identity_tokens
                SET state = 'revoked', revoked_at = ?
                WHERE token_id = ? AND state = 'active'
                """,
                (_format_time(now), token_id),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    "SELECT token_id FROM mcp_identity_tokens WHERE token_id = ?",
                    (token_id,),
                ).fetchone()
                if existing is None:
                    raise KeyError(f"MCP identity token not found: {token_id}")
        return self.get(token_id)


def _validate_user_subject(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 256 or any(ord(character) < 32 for character in value):
        raise ValueError("user_subject is invalid")
    return value


def _validate_expected_principal(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 256 or any(ord(character) < 32 for character in value):
        raise ValueError("expected_principal_ref is invalid")
    return value


def _validate_label(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 120 or any(ord(character) < 32 for character in value):
        raise ValueError("MCP identity token label is invalid")
    return value


def _validate_scopes(scopes: list[str]) -> list[str]:
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("MCP identity token scopes are required")
    normalized = sorted({str(scope).strip() for scope in scopes})
    if not set(normalized).issubset(_ALLOWED_SCOPES):
        raise ValueError("MCP identity token contains an unsupported scope")
    return normalized


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _record_from_row(row: sqlite3.Row) -> dict:
    return {
        "token_id": row["token_id"],
        "user_subject": row["user_subject"],
        "expected_principal_ref": row["expected_principal_ref"],
        "label": row["label"],
        "scopes": json.loads(row["scopes_json"]),
        "state": row["state"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_time(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))
