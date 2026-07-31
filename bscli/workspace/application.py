from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from bscli.core.central_service import CentralCapabilityService
from bscli.workspace.gateway import GatewayRequestError, OpenClawGatewayClient


@dataclass(frozen=True)
class WorkspaceChatResult:
    run_id: str | None
    status: str


class WorkspaceApplication:
    def __init__(
        self,
        *,
        service: CentralCapabilityService,
        gateway: OpenClawGatewayClient | None = None,
    ) -> None:
        self.service = service
        self.store = service.workspace
        self.gateway = gateway

    def start_enrollment(self) -> dict:
        return self.store.start_link()

    def enrollment_status(self, enrollment_token: str) -> dict | None:
        return self.store.link_status(enrollment_token)

    def complete_enrollment(
        self,
        *,
        enrollment_token: str,
        username: str,
        password: str,
    ) -> dict:
        account = self.store.create_account(
            enrollment_token=enrollment_token,
            username=username,
            password=password,
        )
        registered = self.service.register_workspace_endpoint(
            account_id=account["account_id"],
        )
        session = self.store.create_session(account["account_id"])
        return {
            "account": _public_account(registered["account"]),
            "session": session,
        }

    def login(self, *, username: str, password: str) -> dict | None:
        account = self.store.authenticate(
            username=username,
            password=password,
        )
        if account is None:
            return None
        account = self.store.record_login(account["account_id"])
        session = self.store.create_session(account["account_id"])
        return {
            "account": _public_account(account),
            "session": session,
        }

    def session(
        self,
        session_token: str | None,
        *,
        csrf_token: str | None = None,
        touch: bool = True,
    ) -> dict | None:
        return self.store.verify_session(
            session_token,
            csrf_token=csrf_token,
            touch=touch,
        )

    @staticmethod
    def public_account(account: dict | None) -> dict | None:
        return _public_account(account) if account else None

    def logout(self, session_token: str | None) -> None:
        self.store.revoke_session(session_token)

    def list_tasks(
        self,
        account: dict,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        return [
            _public_task(task)
            for task in self.service.tasks.list_tasks(
                user_subject=account["user_subject"],
                active_only=active_only,
                limit=limit,
            )
        ]

    def task_detail(self, account: dict, task_id: str) -> dict:
        task = self.service.tasks.get_task(
            task_id,
            user_subject=account["user_subject"],
        )
        events = self.service.tasks.list_events(
            task_id=task_id,
            user_subject=account["user_subject"],
            limit=200,
        )
        interaction = None
        if task.get("current_interaction_id"):
            try:
                response = self.service.present_interaction(
                    user_subject=account["user_subject"],
                    agent_host="openclaw",
                    endpoint_key=account["endpoint_key"],
                    interaction_id=task["current_interaction_id"],
                )
                interaction = response["interaction"]
            except (KeyError, RuntimeError):
                interaction = None
        return {
            "task": _public_task(task),
            "events": [_public_event(event) for event in events],
            "interaction": interaction,
        }

    def list_events(
        self,
        account: dict,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return [
            _public_event(event)
            for event in self.service.tasks.list_user_events(
                user_subject=account["user_subject"],
                after_event_id=after_event_id,
                limit=limit,
            )
        ]

    def latest_event_id(self, account: dict) -> str | None:
        return self.service.tasks.latest_user_event_id(
            user_subject=account["user_subject"],
        )

    def event_cursor(self, account: dict) -> str:
        return self.service.tasks.current_user_event_cursor(
            user_subject=account["user_subject"],
        )

    def list_timeline(
        self,
        account: dict,
        *,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        endpoints = {
            endpoint["endpoint_id"]: endpoint
            for endpoint in self.service.tasks.list_endpoints(
                user_subject=account["user_subject"],
                active_only=False,
                limit=100,
            )
        }
        return [
            _public_timeline_entry(
                entry,
                source=endpoints.get(entry.get("source_endpoint_id")),
                is_origin=(
                    entry.get("source_endpoint_id") == account.get("endpoint_id")
                ),
            )
            for entry in self.service.tasks.list_timeline(
                user_subject=account["user_subject"],
                after_sequence=after_sequence,
                limit=limit,
            )
        ]

    def timeline_cursor(self, account: dict) -> int:
        return self.service.tasks.latest_timeline_sequence(
            user_subject=account["user_subject"],
        )

    def list_endpoints(self, account: dict) -> list[dict]:
        return [
            _public_endpoint(endpoint)
            for endpoint in self.service.tasks.list_endpoints(
                user_subject=account["user_subject"],
                active_only=False,
                limit=100,
            )
        ]

    def gateway_status(self) -> dict:
        if self.gateway is None:
            return {
                "available": False,
                "code": "GATEWAY_NOT_CONFIGURED",
            }
        try:
            result = self.gateway.call(
                "system.info",
                {},
                timeout_seconds=8,
            )
        except GatewayRequestError as exc:
            return {
                "available": False,
                "code": exc.code,
            }
        return {
            "available": True,
            "version": _safe_text(
                result.get("version") if isinstance(result, dict) else None,
                80,
            ),
        }

    def chat_history(self, account: dict, *, limit: int = 100) -> dict:
        gateway = self._gateway()
        result = gateway.call(
            "chat.history",
            {
                "sessionKey": account["openclaw_session_key"],
                "limit": min(max(int(limit), 1), 200),
                "maxChars": 200_000,
            },
            timeout_seconds=20,
        )
        return {
            "messages": _visible_messages(result),
            "sessionId": (
                _safe_text(result.get("sessionId"), 256)
                if isinstance(result, dict)
                else None
            ),
        }

    def chat_stream(
        self,
        account: dict,
        *,
        timeout_seconds: float = 25,
    ) -> Iterator[dict[str, Any]]:
        return self._gateway().stream(
            session_key=account["openclaw_session_key"],
            timeout_seconds=timeout_seconds,
        )

    def send_chat_stream(
        self,
        account: dict,
        *,
        message: str,
        idempotency_key: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        message = str(message or "").strip()
        if not message or len(message) > 20_000:
            raise ValueError("chat message is empty or too long")
        effective_key = _safe_text(idempotency_key, 128) or str(uuid4())
        self._append_workspace_message(
            account,
            role="user",
            text=message,
            message_key=f"workspace:user:{effective_key}",
        )
        grant = self.store.issue_gateway_grant(account["account_id"])
        source = self._gateway().send_stream(
            session_key=grant["session_key"],
            endpoint_key=grant["endpoint_key"],
            grant=grant["grant"],
            message=message,
            idempotency_key=effective_key,
            timeout_seconds=150,
        )

        def synchronized_stream() -> Iterator[dict[str, Any]]:
            for item in source:
                if (
                    item.get("type") == "chat"
                    and item.get("state") == "final"
                    and isinstance(item.get("text"), str)
                    and item["text"].strip()
                ):
                    self._append_workspace_message(
                        account,
                        role="assistant",
                        text=item["text"],
                        message_key=(
                            "workspace:assistant:"
                            f"{_safe_text(item.get('runId'), 256) or effective_key}"
                        ),
                    )
                yield item

        return synchronized_stream()

    def send_chat(
        self,
        account: dict,
        *,
        message: str,
        idempotency_key: str | None = None,
    ) -> WorkspaceChatResult:
        message = str(message or "").strip()
        if not message or len(message) > 20_000:
            raise ValueError("chat message is empty or too long")
        gateway = self._gateway()
        effective_key = _safe_text(idempotency_key, 128) or str(uuid4())
        self._append_workspace_message(
            account,
            role="user",
            text=message,
            message_key=f"workspace:user:{effective_key}",
        )
        grant = self.store.issue_gateway_grant(account["account_id"])
        gateway.call(
            "agentbridge.workspace.bind",
            {
                "sessionKey": grant["session_key"],
                "endpointKey": grant["endpoint_key"],
                "grant": grant["grant"],
            },
            timeout_seconds=20,
        )
        result = gateway.call(
            "chat.send",
            {
                "sessionKey": grant["session_key"],
                "message": message,
                "deliver": False,
                "idempotencyKey": effective_key,
                "timeoutMs": 120_000,
            },
            timeout_seconds=30,
        )
        result = result if isinstance(result, dict) else {}
        return WorkspaceChatResult(
            run_id=_safe_text(
                result.get("runId") or result.get("run_id"),
                256,
            ),
            status=_safe_text(result.get("status"), 80) or "accepted",
        )

    def _append_workspace_message(
        self,
        account: dict,
        *,
        role: str,
        text: str,
        message_key: str,
    ) -> None:
        endpoint_id = _safe_text(account.get("endpoint_id"), 128)
        if not endpoint_id:
            return
        try:
            self.service.tasks.append_timeline_message(
                user_subject=account["user_subject"],
                source_endpoint_id=endpoint_id,
                message_key=message_key,
                role=role,
                text=text,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error):
            # Chat remains available if the optional cross-end projection fails.
            return

    def _gateway(self) -> OpenClawGatewayClient:
        if self.gateway is None:
            raise GatewayRequestError(
                "GATEWAY_NOT_CONFIGURED",
                "OpenClaw Gateway is not configured.",
            )
        return self.gateway


def _public_account(account: dict) -> dict:
    return {
        "accountId": account["account_id"],
        "username": account["username"],
        "state": account["state"],
        "lastLoginAt": account["last_login_at"],
    }


def _public_task(task: dict) -> dict:
    names = (
        "task_id",
        "agent_host",
        "title",
        "status",
        "summary",
        "current_operation_id",
        "current_interaction_id",
        "version",
        "created_at",
        "updated_at",
        "finished_at",
    )
    return {name: task.get(name) for name in names}


def _public_event(event: dict) -> dict:
    names = (
        "event_id",
        "task_id",
        "event_type",
        "created_at",
    )
    return {name: event.get(name) for name in names}


def _public_timeline_entry(
    entry: dict,
    *,
    source: dict | None,
    is_origin: bool,
) -> dict:
    client_type = str((source or {}).get("client_type") or "system")
    display_label = (source or {}).get("label") or {
        "web": "Agent Workspace",
        "webchat": "Agent Workspace",
        "telegram": "Telegram",
        "openclaw-weixin": "微信",
        "system": "AgentBridge",
    }.get(client_type, "已关联客户端")
    return {
        "entry_id": entry["entry_id"],
        "sequence": entry["sequence"],
        "entry_type": entry["entry_type"],
        "task_id": entry.get("task_id"),
        "role": entry.get("role"),
        "text": entry.get("text"),
        "payload": entry.get("payload") or {},
        "created_at": entry["created_at"],
        "source": {
            "client_type": client_type,
            "display_label": display_label,
            "is_origin": bool(is_origin),
        },
    }


def _public_endpoint(endpoint: dict) -> dict:
    client_type = str(endpoint.get("client_type") or "unknown")
    display_label = endpoint.get("label") or {
        "web": "Agent Workspace",
        "telegram": "Telegram",
        "openclaw-weixin": "微信",
    }.get(client_type, "已关联客户端")
    return {
        "endpoint_id": endpoint.get("endpoint_id"),
        "client_type": client_type,
        "display_label": display_label,
        "capabilities": list(endpoint.get("capabilities") or []),
        "state": endpoint.get("state"),
        "created_at": endpoint.get("created_at"),
        "updated_at": endpoint.get("updated_at"),
        "last_seen_at": endpoint.get("last_seen_at"),
    }


def _visible_messages(result: Any) -> list[dict]:
    if not isinstance(result, dict):
        return []
    values = result.get("messages")
    if not isinstance(values, list):
        return []
    messages: list[dict] = []
    for value in values[-200:]:
        if not isinstance(value, dict):
            continue
        role = str(value.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(value.get("content"))
        if not text:
            continue
        messages.append(
            {
                "id": _safe_text(
                    value.get("id") or value.get("messageId"),
                    256,
                ),
                "role": role,
                "text": text,
                "timestamp": _safe_text(
                    value.get("timestamp")
                    or value.get("createdAt")
                    or value.get("created_at"),
                    80,
                ),
            }
        )
    return messages


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content[:50_000]
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if sum(len(part) for part in parts) >= 50_000:
            break
    return "\n".join(parts)[:50_000]


def _safe_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    normalized = str(value).strip()
    return normalized[:maximum] if normalized else None
