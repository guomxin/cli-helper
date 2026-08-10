from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Callable, Iterator
from uuid import uuid4


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
_LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LINK_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{8}$")
_PASSWORD_MIN_LENGTH = 12
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class WorkspaceConflictError(RuntimeError):
    pass


class WorkspaceLinkError(RuntimeError):
    pass


class WorkspaceStore:
    """Persistent accounts, browser sessions, and trusted endpoint linking."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
        session_ttl_seconds: int = 30 * 24 * 3600,
        session_idle_seconds: int = 7 * 24 * 3600,
        link_ttl_seconds: int = 10 * 60,
        gateway_grant_ttl_seconds: int = 90,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.session_ttl_seconds = session_ttl_seconds
        self.session_idle_seconds = session_idle_seconds
        self.link_ttl_seconds = link_ttl_seconds
        self.gateway_grant_ttl_seconds = gateway_grant_ttl_seconds
        if session_idle_seconds < 60 or session_ttl_seconds < session_idle_seconds:
            raise ValueError("invalid workspace session lifetime")
        if link_ttl_seconds < 60 or gateway_grant_ttl_seconds < 15:
            raise ValueError("invalid workspace challenge lifetime")
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspace_accounts (
                    account_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    user_subject TEXT NOT NULL UNIQUE,
                    endpoint_id TEXT,
                    endpoint_key TEXT NOT NULL UNIQUE,
                    openclaw_session_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS workspace_sessions (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    csrf_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    idle_expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS workspace_sessions_account_state
                ON workspace_sessions (account_id, revoked_at, expires_at);

                CREATE TABLE IF NOT EXISTS workspace_link_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    enrollment_hash TEXT NOT NULL UNIQUE,
                    code_hash TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    user_subject TEXT,
                    approver_endpoint_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS workspace_links_state_expiry
                ON workspace_link_challenges (state, expires_at);

                CREATE TABLE IF NOT EXISTS workspace_gateway_grants (
                    grant_id TEXT PRIMARY KEY,
                    grant_hash TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    endpoint_key TEXT NOT NULL,
                    openclaw_session_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS workspace_gateway_turns (
                    user_subject TEXT NOT NULL,
                    endpoint_key TEXT NOT NULL,
                    openclaw_session_key TEXT NOT NULL,
                    turn_ref TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_subject, endpoint_key)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS workspace_gateway_turns_session
                ON workspace_gateway_turns (user_subject, openclaw_session_key);
                """
            )

    def start_link(self) -> dict:
        now = self._now()
        expires = now + timedelta(seconds=self.link_ttl_seconds)
        for _attempt in range(8):
            challenge_id = str(uuid4())
            enrollment_token = f"abwe_{secrets.token_urlsafe(32)}"
            code = "".join(
                secrets.choice(_LINK_CODE_ALPHABET) for _ in range(8)
            )
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO workspace_link_challenges (
                            challenge_id, enrollment_hash, code_hash, state,
                            created_at, expires_at
                        ) VALUES (?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            challenge_id,
                            _sha256(enrollment_token),
                            _sha256(code),
                            _format_time(now),
                            _format_time(expires),
                        ),
                    )
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise RuntimeError("could not allocate a workspace link code")
        return {
            "challenge_id": challenge_id,
            "enrollment_token": enrollment_token,
            "link_code": code,
            "state": "pending",
            "expires_at": _format_time(expires),
        }

    def link_status(self, enrollment_token: str) -> dict | None:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workspace_link_challenges
                WHERE enrollment_hash = ?
                """,
                (_sha256(enrollment_token),),
            ).fetchone()
            if row is None:
                return None
            if row["state"] in {"pending", "confirmed"} and _parse_time(
                row["expires_at"]
            ) <= now:
                connection.execute(
                    """
                    UPDATE workspace_link_challenges
                    SET state = 'expired'
                    WHERE challenge_id = ?
                    """,
                    (row["challenge_id"],),
                )
                return {
                    "challenge_id": row["challenge_id"],
                    "state": "expired",
                    "expires_at": row["expires_at"],
                }
        return _link_from_row(row)

    def confirm_link(
        self,
        *,
        link_code: str,
        user_subject: str,
        approver_endpoint_id: str,
    ) -> dict:
        code = str(link_code or "").strip().upper().replace("-", "")
        if not _LINK_CODE_RE.fullmatch(code):
            raise WorkspaceLinkError("invalid workspace link code")
        user_subject = _required_text(user_subject, "user_subject", 256)
        approver_endpoint_id = _required_text(
            approver_endpoint_id,
            "approver_endpoint_id",
            128,
        )
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM workspace_link_challenges
                WHERE code_hash = ?
                """,
                (_sha256(code),),
            ).fetchone()
            if row is None:
                raise WorkspaceLinkError("workspace link code was not found")
            if row["state"] != "pending":
                raise WorkspaceLinkError(
                    f"workspace link is no longer pending: {row['state']}"
                )
            if _parse_time(row["expires_at"]) <= now:
                connection.execute(
                    """
                    UPDATE workspace_link_challenges
                    SET state = 'expired'
                    WHERE challenge_id = ?
                    """,
                    (row["challenge_id"],),
                )
                raise WorkspaceLinkError("workspace link code has expired")
            existing = connection.execute(
                """
                SELECT account_id FROM workspace_accounts
                WHERE user_subject = ? AND state = 'active'
                """,
                (user_subject,),
            ).fetchone()
            if existing is not None:
                raise WorkspaceConflictError(
                    "this AgentBridge identity already has a workspace account"
                )
            confirmed_at = _format_time(now)
            connection.execute(
                """
                UPDATE workspace_link_challenges
                SET state = 'confirmed', user_subject = ?,
                    approver_endpoint_id = ?, confirmed_at = ?
                WHERE challenge_id = ?
                """,
                (
                    user_subject,
                    approver_endpoint_id,
                    confirmed_at,
                    row["challenge_id"],
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM workspace_link_challenges
                WHERE challenge_id = ?
                """,
                (row["challenge_id"],),
            ).fetchone()
        return _link_from_row(row)

    def create_account(
        self,
        *,
        enrollment_token: str,
        username: str,
        password: str,
    ) -> dict:
        username = _validate_username(username)
        password_hash = _hash_password(password)
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            link = connection.execute(
                """
                SELECT * FROM workspace_link_challenges
                WHERE enrollment_hash = ?
                """,
                (_sha256(enrollment_token),),
            ).fetchone()
            if link is None:
                raise WorkspaceLinkError(
                    "workspace identity link has not been confirmed"
                )
            if link["state"] == "consumed":
                existing = connection.execute(
                    """
                    SELECT * FROM workspace_accounts
                    WHERE user_subject = ? AND state = 'active'
                    """,
                    (link["user_subject"],),
                ).fetchone()
                if (
                    existing is not None
                    and existing["username"].casefold() == username.casefold()
                    and _verify_password(password, existing["password_hash"])
                ):
                    return _account_from_row(existing)
                raise WorkspaceConflictError(
                    "workspace enrollment was already consumed"
                )
            if link["state"] != "confirmed":
                raise WorkspaceLinkError(
                    "workspace identity link has not been confirmed"
                )
            if _parse_time(link["expires_at"]) <= now:
                connection.execute(
                    """
                    UPDATE workspace_link_challenges
                    SET state = 'expired'
                    WHERE challenge_id = ?
                    """,
                    (link["challenge_id"],),
                )
                raise WorkspaceLinkError("workspace identity link has expired")
            account_id = str(uuid4())
            endpoint_key = f"workspace:{account_id}"
            session_key = (
                f"agent:main:agentbridge-workspace:direct:{account_id}"
            )
            timestamp = _format_time(now)
            try:
                connection.execute(
                    """
                    INSERT INTO workspace_accounts (
                        account_id, username, password_hash, user_subject,
                        endpoint_key, openclaw_session_key, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        account_id,
                        username,
                        password_hash,
                        link["user_subject"],
                        endpoint_key,
                        session_key,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkspaceConflictError(
                    "workspace username or identity is already registered"
                ) from exc
            connection.execute(
                """
                UPDATE workspace_link_challenges
                SET state = 'consumed', consumed_at = ?
                WHERE challenge_id = ?
                """,
                (timestamp, link["challenge_id"]),
            )
        return self.get_account(account_id)

    def attach_endpoint(self, *, account_id: str, endpoint_id: str) -> dict:
        now = _format_time(self._now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workspace_accounts
                SET endpoint_id = ?, updated_at = ?
                WHERE account_id = ? AND state = 'active'
                """,
                (endpoint_id, now, account_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"workspace account not found: {account_id}")
        return self.get_account(account_id)

    def authenticate(self, *, username: str, password: str) -> dict | None:
        username = str(username or "").strip()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workspace_accounts
                WHERE username = ? COLLATE NOCASE
                """,
                (username,),
            ).fetchone()
        if row is None:
            _verify_password(
                password,
                _hash_password("AgentBridge-dummy-password"),
            )
            return None
        if row["state"] != "active":
            return None
        return (
            _account_from_row(row)
            if _verify_password(password, row["password_hash"])
            else None
        )

    def record_login(self, account_id: str) -> dict:
        now = _format_time(self._now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workspace_accounts
                SET last_login_at = ?, updated_at = ?
                WHERE account_id = ? AND state = 'active'
                """,
                (now, now, account_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"workspace account not found: {account_id}")
        return self.get_account(account_id)

    def get_account(self, account_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workspace_accounts
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"workspace account not found: {account_id}")
        return _account_from_row(row)

    def resolve_gateway_session(
        self,
        *,
        user_subject: str,
        session_key: str,
    ) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workspace_accounts
                WHERE user_subject = ? AND openclaw_session_key = ?
                  AND state = 'active'
                """,
                (user_subject, session_key),
            ).fetchone()
        if row is None:
            raise WorkspaceLinkError(
                "workspace gateway session does not belong to this identity"
            )
        return _account_from_row(row)

    def create_session(self, account_id: str) -> dict:
        account = self.get_account(account_id)
        if account["state"] != "active":
            raise PermissionError("workspace account is disabled")
        now = self._now()
        session_token = f"abws_{secrets.token_urlsafe(32)}"
        csrf_token = secrets.token_urlsafe(32)
        session_id = str(uuid4())
        expires_at = now + timedelta(seconds=self.session_ttl_seconds)
        idle_expires_at = min(
            expires_at,
            now + timedelta(seconds=self.session_idle_seconds),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspace_sessions (
                    session_id, account_id, token_hash, csrf_hash,
                    created_at, last_seen_at, expires_at, idle_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    account_id,
                    _sha256(session_token),
                    _sha256(csrf_token),
                    _format_time(now),
                    _format_time(now),
                    _format_time(expires_at),
                    _format_time(idle_expires_at),
                ),
            )
        return {
            "session_id": session_id,
            "session_token": session_token,
            "csrf_token": csrf_token,
            "account": account,
            "expires_at": _format_time(expires_at),
            "idle_expires_at": _format_time(idle_expires_at),
        }

    def verify_session(
        self,
        session_token: str | None,
        *,
        csrf_token: str | None = None,
        touch: bool = True,
    ) -> dict | None:
        if not session_token:
            return None
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    a.account_id,
                    a.username,
                    a.user_subject,
                    a.endpoint_id,
                    a.endpoint_key,
                    a.openclaw_session_key,
                    a.state,
                    a.created_at,
                    a.updated_at,
                    a.last_login_at,
                    s.session_id,
                    s.csrf_hash,
                    s.expires_at AS session_expires_at,
                    s.idle_expires_at AS session_idle_expires_at,
                    s.revoked_at
                FROM workspace_sessions AS s
                JOIN workspace_accounts AS a ON a.account_id = s.account_id
                WHERE s.token_hash = ?
                """,
                (_sha256(session_token),),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["state"] != "active"
                or _parse_time(row["session_expires_at"]) <= now
                or _parse_time(row["session_idle_expires_at"]) <= now
            ):
                return None
            if csrf_token is not None and not hmac.compare_digest(
                row["csrf_hash"],
                _sha256(csrf_token),
            ):
                return None
            if touch:
                idle_expires = min(
                    _parse_time(row["session_expires_at"]),
                    now + timedelta(seconds=self.session_idle_seconds),
                )
                connection.execute(
                    """
                    UPDATE workspace_sessions
                    SET last_seen_at = ?, idle_expires_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        _format_time(now),
                        _format_time(idle_expires),
                        row["session_id"],
                    ),
                )
        account = _account_from_row(row)
        return {
            **account,
            "session_id": row["session_id"],
            "session_expires_at": row["session_expires_at"],
            "session_idle_expires_at": row["session_idle_expires_at"],
        }

    def revoke_session(self, session_token: str | None) -> None:
        if not session_token:
            return
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workspace_sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (_format_time(self._now()), _sha256(session_token)),
            )

    def issue_gateway_grant(self, account_id: str) -> dict:
        account = self.get_account(account_id)
        if not account["endpoint_id"]:
            raise WorkspaceLinkError("workspace endpoint is not registered")
        now = self._now()
        expires = now + timedelta(seconds=self.gateway_grant_ttl_seconds)
        raw_grant = f"abwg_{secrets.token_urlsafe(32)}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspace_gateway_grants (
                    grant_id, grant_hash, account_id, user_subject,
                    endpoint_key, openclaw_session_key, state,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    str(uuid4()),
                    _sha256(raw_grant),
                    account["account_id"],
                    account["user_subject"],
                    account["endpoint_key"],
                    account["openclaw_session_key"],
                    _format_time(now),
                    _format_time(expires),
                ),
            )
        return {
            "grant": raw_grant,
            "endpoint_key": account["endpoint_key"],
            "session_key": account["openclaw_session_key"],
            "expires_at": _format_time(expires),
        }

    def redeem_gateway_grant(
        self,
        *,
        grant: str,
        user_subject: str,
        endpoint_key: str,
        session_key: str,
        turn_ref: str | None = None,
    ) -> dict:
        normalized_turn_ref = (
            _required_text(turn_ref, "turn_ref", 128)
            if turn_ref is not None
            else None
        )
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM workspace_gateway_grants
                WHERE grant_hash = ?
                """,
                (_sha256(grant),),
            ).fetchone()
            if row is None or row["state"] != "pending":
                raise WorkspaceLinkError("workspace gateway grant is invalid")
            if _parse_time(row["expires_at"]) <= now:
                connection.execute(
                    """
                    UPDATE workspace_gateway_grants
                    SET state = 'expired'
                    WHERE grant_id = ?
                    """,
                    (row["grant_id"],),
                )
                raise WorkspaceLinkError("workspace gateway grant has expired")
            if (
                row["user_subject"] != user_subject
                or row["endpoint_key"] != endpoint_key
                or row["openclaw_session_key"] != session_key
            ):
                raise PermissionError(
                    "workspace gateway grant belongs to another identity"
                )
            consumed_at = _format_time(now)
            connection.execute(
                """
                UPDATE workspace_gateway_grants
                SET state = 'consumed', consumed_at = ?
                WHERE grant_id = ?
                """,
                (consumed_at, row["grant_id"]),
            )
            if normalized_turn_ref is not None:
                connection.execute(
                    """
                    INSERT INTO workspace_gateway_turns (
                        user_subject, endpoint_key, openclaw_session_key,
                        turn_ref, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_subject, endpoint_key) DO UPDATE SET
                        openclaw_session_key = excluded.openclaw_session_key,
                        turn_ref = excluded.turn_ref,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["user_subject"],
                        row["endpoint_key"],
                        row["openclaw_session_key"],
                        normalized_turn_ref,
                        consumed_at,
                    ),
                )
        return {
            "status": "succeeded",
            "account_id": row["account_id"],
            "endpoint_key": row["endpoint_key"],
            "session_key": row["openclaw_session_key"],
            "turn_ref": normalized_turn_ref,
        }

    def resolve_gateway_turn(
        self,
        *,
        user_subject: str,
        endpoint_key: str,
        session_key: str,
    ) -> dict | None:
        user_subject = _required_text(user_subject, "user_subject", 256)
        endpoint_key = _required_text(endpoint_key, "endpoint_key", 768)
        session_key = _required_text(session_key, "session_key", 1024)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT t.*
                FROM workspace_gateway_turns AS t
                JOIN workspace_accounts AS a
                  ON a.user_subject = t.user_subject
                 AND a.endpoint_key = t.endpoint_key
                 AND a.openclaw_session_key = t.openclaw_session_key
                WHERE t.user_subject = ? AND t.endpoint_key = ?
                  AND t.openclaw_session_key = ? AND a.state = 'active'
                """,
                (user_subject, endpoint_key, session_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_subject": row["user_subject"],
            "endpoint_key": row["endpoint_key"],
            "session_key": row["openclaw_session_key"],
            "turn_ref": row["turn_ref"],
            "updated_at": row["updated_at"],
        }

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _account_from_row(row: sqlite3.Row) -> dict:
    return {
        "account_id": row["account_id"],
        "username": row["username"],
        "user_subject": row["user_subject"],
        "endpoint_id": row["endpoint_id"],
        "endpoint_key": row["endpoint_key"],
        "openclaw_session_key": row["openclaw_session_key"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
    }


def _link_from_row(row: sqlite3.Row) -> dict:
    return {
        "challenge_id": row["challenge_id"],
        "state": row["state"],
        "user_subject": row["user_subject"],
        "approver_endpoint_id": row["approver_endpoint_id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "confirmed_at": row["confirmed_at"],
        "consumed_at": row["consumed_at"],
    }


def _validate_username(value: str) -> str:
    normalized = str(value or "").strip()
    if not _USERNAME_RE.fullmatch(normalized):
        raise ValueError(
            "username must start with a letter and contain 3-64 letters, digits, dots, underscores, or hyphens"
        )
    return normalized


def _hash_password(password: str) -> str:
    password = str(password or "")
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"password must contain at least {_PASSWORD_MIN_LENGTH} characters"
        )
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(
            digest,
            base64.urlsafe_b64decode(expected),
        )
    except (ValueError, TypeError):
        return False


def _required_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, (str, int)):
        raise ValueError(f"{name} is required")
    normalized = str(value).strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
