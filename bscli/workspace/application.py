from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3
import threading
from typing import Any, Iterator
from uuid import uuid4

from bscli.core.central_service import CentralCapabilityService
from bscli.core.timeline_attachments import (
    TimelineAttachmentExpired,
    TimelineAttachmentIntegrityError,
    TimelineAttachmentNotFound,
    public_attachment,
)
from bscli.workspace.gateway import GatewayRequestError, OpenClawGatewayClient


_LOG = logging.getLogger(__name__)

MAX_CHAT_ATTACHMENTS = 4
MAX_CHAT_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_CHAT_ATTACHMENTS_TOTAL_BYTES = 12 * 1024 * 1024
_CHAT_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/webp": (b"RIFF", ".webp"),
}


class WorkspaceArtifactError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        self._chat_state_lock = threading.Lock()
        self._active_chat_accounts: set[str] = set()

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
        account = self.store.verify_session(
            session_token,
            csrf_token=csrf_token,
            touch=touch,
        )
        if account is not None and touch and account.get("endpoint_id"):
            try:
                self.service.tasks.touch_endpoint(
                    endpoint_id=account["endpoint_id"],
                    user_subject=account["user_subject"],
                )
            except (KeyError, RuntimeError, ValueError, sqlite3.Error):
                # Session verification remains available during optional
                # Task Hub projection failures.
                pass
        return account

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
        artifacts = self.service.tasks.list_artifacts(
            task_id=task_id,
            user_subject=account["user_subject"],
            limit=100,
        )
        artifacts.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("artifact_id") or ""),
            )
        )
        interactions = []
        for linked in self.service.tasks.list_task_interactions(
            task_id=task_id,
            user_subject=account["user_subject"],
            limit=100,
        ):
            try:
                response = self.service.get_interaction(
                    user_subject=account["user_subject"],
                    interaction_id=linked["interaction_id"],
                )
                interaction = response["interaction"]
                if interaction.get("state") in {"pending", "processing"}:
                    response = self.service.present_interaction(
                        user_subject=account["user_subject"],
                        agent_host="openclaw",
                        endpoint_key=account["endpoint_key"],
                        interaction_id=linked["interaction_id"],
                    )
                    interaction = response["interaction"]
                interactions.append(
                    {
                        **interaction,
                        "linkedAt": linked["linked_at"],
                        "lastObservedAt": linked["last_observed_at"],
                    }
                )
            except (KeyError, RuntimeError):
                continue
        interaction = next(
            (
                item
                for item in interactions
                if item.get("interactionId")
                == task.get("current_interaction_id")
            ),
            None,
        )
        return {
            "task": _public_task(task),
            "events": [_public_event(event) for event in events],
            "artifacts": [_public_artifact(item) for item in artifacts],
            "interaction": interaction,
            "interactions": interactions,
        }

    def artifact_history(self, account: dict, *, limit: int = 20) -> list[dict]:
        limit = min(max(int(limit), 1), 50)
        artifacts = self.service.tasks.list_user_artifacts(
            user_subject=account["user_subject"],
            limit=min(max(limit * 10, 100), 500),
        )
        artifacts.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        grouped: dict[str, list[dict]] = {}
        for artifact in artifacts:
            grouped.setdefault(artifact["task_id"], []).append(artifact)
        items = []
        for task_id, task_artifacts in grouped.items():
            if len(items) >= limit:
                break
            task = self.service.tasks.get_task(
                task_id,
                user_subject=account["user_subject"],
            )
            task_artifacts.sort(
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("artifact_id") or ""),
                )
            )
            items.append(
                {
                    "task": _public_task(task),
                    "events": [],
                    "artifacts": [
                        _public_artifact(item) for item in task_artifacts
                    ],
                    "interaction": None,
                }
            )
        return items

    def reissue_artifact(
        self,
        account: dict,
        *,
        task_id: str,
        artifact_id: str,
    ) -> dict:
        result = self.service.reissue_document_download(
            user_subject=account["user_subject"],
            task_id=task_id,
            artifact_id=artifact_id,
        )
        if result.get("status") != "succeeded":
            code = str(result.get("error", {}).get("code") or "REISSUE_FAILED")
            messages = {
                "LOGIN_REQUIRED": "对应系统登录已失效，请先登录后再重新生成下载。",
                "DOWNLOAD_NOT_FOUND": "原文件记录不存在，无法重新生成下载。",
                "DOWNLOAD_ACCESS_DENIED": "无权重新生成这份文件。",
                "DOWNLOAD_INTEGRITY_FAILED": "原文件记录校验失败，无法重新生成下载。",
                "ARTIFACT_REISSUE_UNSUPPORTED": "这个文件暂不支持重新生成下载。",
                "ARTIFACT_REISSUE_CONFLICT": "文件已在另一端更新，请刷新后重试。",
                "REPORT_REGENERATION_FAILED": "报告数据暂时无法重新读取，请稍后再试。",
            }
            raise WorkspaceArtifactError(
                code,
                messages.get(code, "重新生成文件失败，请稍后再试。"),
            )
        return self.task_detail(account, task_id)

    def continue_task(self, account: dict, task_id: str) -> dict:
        response = self.service.resolve_host_task_continuation(
            user_subject=account["user_subject"],
            agent_host="openclaw",
            endpoint_key=account["endpoint_key"],
            task_id=task_id,
            reuse_selected=False,
            allow_follow_up=False,
        )
        if response.get("status") != "selected":
            raise KeyError(f"task not found: {task_id}")
        task = response["task"]
        return {
            "status": "selected",
            "task": {
                "task_id": task["taskId"],
                "title": task["title"],
                "status": task["status"],
            },
            "continuation": response["continuation"],
            "message": f"继续刚才选择的“{task['title']}”任务。",
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
        before_sequence: int | None = None,
        entry_type: str | None = None,
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
                before_sequence=before_sequence,
                entry_type=entry_type,
                limit=limit,
            )
        ]

    def timeline_cursor(self, account: dict) -> int:
        return self.service.tasks.latest_timeline_sequence(
            user_subject=account["user_subject"],
        )

    def timeline_attachment(self, account: dict, attachment_id: str) -> dict:
        try:
            attachment = self.service.timeline_attachments.ready_payload(
                attachment_id
            )
        except TimelineAttachmentNotFound as exc:
            raise WorkspaceArtifactError(
                "TIMELINE_ATTACHMENT_NOT_FOUND",
                "The timeline attachment was not found.",
            ) from exc
        except TimelineAttachmentExpired as exc:
            raise WorkspaceArtifactError(
                "TIMELINE_ATTACHMENT_EXPIRED",
                "The timeline attachment has expired.",
            ) from exc
        except TimelineAttachmentIntegrityError as exc:
            raise WorkspaceArtifactError(
                "TIMELINE_ATTACHMENT_INTEGRITY_FAILED",
                "The timeline attachment failed its integrity check.",
            ) from exc
        if attachment["user_subject"] != account["user_subject"]:
            raise PermissionError("timeline attachment belongs to another user")
        return attachment

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
        attachments: list[dict] | None = None,
    ) -> Iterator[dict[str, Any]]:
        message = str(message or "").strip()
        normalized_attachments = _validated_chat_attachments(attachments)
        if not message and normalized_attachments:
            message = "请处理附加图片中的内容。"
        if not message or len(message) > 20_000:
            raise ValueError("chat message is empty or too long")
        effective_key = _safe_text(idempotency_key, 128) or str(uuid4())

        def synchronized_stream() -> Iterator[dict[str, Any]]:
            source = None
            claimed = False
            assistant_recorded = False
            accepted_run_id = ""
            had_tool_activity = False
            message_key = f"workspace:user:{effective_key}"
            timeline_attachments = []
            if normalized_attachments:
                stored = self.service.timeline_attachments.create_many(
                    user_subject=account["user_subject"],
                    message_key=message_key,
                    attachments=normalized_attachments,
                    media_base_url=self.service.trusted_card_base_url,
                )
                timeline_attachments = [
                    public_attachment(item) for item in stored
                ]
            self._append_workspace_message(
                account,
                role="user",
                text=message,
                message_key=message_key,
                payload={"attachments": timeline_attachments},
                required=bool(timeline_attachments),
            )
            try:
                self._claim_chat_account(account)
                claimed = True
                gateway = self._gateway()
                grant = self.store.issue_gateway_grant(account["account_id"])
                source = gateway.send_stream(
                    session_key=grant["session_key"],
                    endpoint_key=grant["endpoint_key"],
                    grant=grant["grant"],
                    message=message,
                    idempotency_key=effective_key,
                    attachments=normalized_attachments,
                    timeout_seconds=300,
                )
                for item in source:
                    if item.get("type") == "accepted":
                        accepted_run_id = _safe_text(item.get("runId"), 256)
                        if int(item.get("attempt") or 0) > 0:
                            self._record_gateway_recovery(
                                account=account,
                                effective_key=effective_key,
                                attempt=int(item.get("attempt") or 0),
                                status="succeeded",
                            )
                        _LOG.info(
                            "Workspace chat accepted account_id=%s run_id=%s "
                            "attempt=%s",
                            _safe_text(account.get("account_id"), 128),
                            accepted_run_id,
                            item.get("attempt", 0),
                        )
                    elif (
                        item.get("type") == "progress"
                        and item.get("kind") == "tool"
                    ):
                        had_tool_activity = True
                    elif item.get("type") == "chat":
                        state = item.get("state")
                        if (
                            state == "final"
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
                            assistant_recorded = True
                        elif state in {"error", "aborted"}:
                            self._append_workspace_failure(
                                account,
                                effective_key=effective_key,
                                text=_terminal_chat_failure_text(item),
                            )
                            assistant_recorded = True
                        if state in {"final", "error", "aborted"}:
                            _LOG.info(
                                "Workspace chat terminal account_id=%s "
                                "run_id=%s state=%s had_tool_activity=%s "
                                "safe_to_retry=%s",
                                _safe_text(account.get("account_id"), 128),
                                _safe_text(item.get("runId"), 256)
                                or accepted_run_id,
                                _safe_text(state, 32),
                                had_tool_activity
                                or item.get("hadToolActivity") is True,
                                item.get("safeToRetry") is True,
                            )
                    yield item
            except GatewayRequestError as exc:
                _LOG.warning(
                    "Workspace chat Gateway failure account_id=%s "
                    "run_id=%s code=%s stage=%s had_tool_activity=%s "
                    "recovery_used=%s recovery_attempt=%s",
                    _safe_text(account.get("account_id"), 128),
                    accepted_run_id,
                    exc.code,
                    _safe_text(exc.details.get("stage"), 80),
                    exc.details.get("hadToolActivity") is True,
                    exc.details.get("recoveryUsed") is True,
                    exc.details.get("recoveryAttempt", 0),
                )
                if exc.details.get("recoveryUsed") is True:
                    self._record_gateway_recovery(
                        account=account,
                        effective_key=effective_key,
                        attempt=int(exc.details.get("recoveryAttempt") or 1),
                        status="failed",
                        error_code=exc.code,
                    )
                if not assistant_recorded:
                    self._append_workspace_failure(
                        account,
                        effective_key=effective_key,
                        text=_workspace_gateway_failure_text(exc),
                    )
                raise
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    close()
                if claimed:
                    self._release_chat_account(account)

        return synchronized_stream()

    def _record_gateway_recovery(
        self,
        *,
        account: dict,
        effective_key: str,
        attempt: int,
        status: str,
        error_code: str | None = None,
    ) -> None:
        try:
            action, reused = self.service.runtime_governance.start_recovery_action(
                action_type="workspace_gateway_pre_accept_retry",
                target_type="workspace_account",
                target_id=str(account["account_id"]),
                actor="agentbridge",
                reason="Workspace Gateway failed before business tool activity.",
                idempotency_key=(
                    f"workspace:{account['account_id']}:{effective_key}:"
                    f"gateway-recovery:{max(attempt, 1)}"
                ),
                side_effect_boundary="B0_NO_EFFECT",
                before={"attempt": max(attempt - 1, 0)},
            )
            if reused and action["status"] != "running":
                return
            self.service.runtime_governance.finish_recovery_action(
                action["action_id"],
                status=status,
                after={"attempt": max(attempt, 1)},
                error_code=error_code,
            )
        except Exception as exc:
            _LOG.warning(
                "Workspace Gateway recovery ledger failed: error=%s",
                exc.__class__.__name__,
            )

    def send_chat(
        self,
        account: dict,
        *,
        message: str,
        idempotency_key: str | None = None,
        attachments: list[dict] | None = None,
    ) -> WorkspaceChatResult:
        run_id = ""
        status = "completed"
        for item in self.send_chat_stream(
            account,
            message=message,
            idempotency_key=idempotency_key,
            attachments=attachments,
        ):
            if item.get("type") != "accepted":
                continue
            run_id = _safe_text(item.get("runId"), 256)
            status = _safe_text(item.get("status"), 80) or "accepted"
        if not run_id:
            raise GatewayRequestError(
                "GATEWAY_RESPONSE_INVALID",
                "OpenClaw Gateway did not accept the Workspace run.",
            )
        return WorkspaceChatResult(run_id=run_id, status=status)

    def _append_workspace_message(
        self,
        account: dict,
        *,
        role: str,
        text: str,
        message_key: str,
        payload: dict[str, Any] | None = None,
        required: bool = False,
    ) -> None:
        endpoint_id = _safe_text(account.get("endpoint_id"), 128)
        if not endpoint_id:
            if required:
                raise RuntimeError("workspace endpoint is unavailable")
            return
        try:
            self.service.tasks.append_timeline_message(
                user_subject=account["user_subject"],
                source_endpoint_id=endpoint_id,
                message_key=message_key,
                role=role,
                text=text,
                payload=payload,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error):
            if required:
                raise
            # Chat remains available if the optional cross-end projection fails.
            return

    def _append_workspace_failure(
        self,
        account: dict,
        *,
        effective_key: str,
        text: str,
    ) -> None:
        self._append_workspace_message(
            account,
            role="assistant",
            text=text,
            message_key=f"workspace:assistant:error:{effective_key}",
        )

    def _claim_chat_account(self, account: dict) -> None:
        account_id = _safe_text(account.get("account_id"), 128)
        if not account_id:
            raise GatewayRequestError(
                "WORKSPACE_IDENTITY_INVALID",
                "Workspace identity is invalid.",
            )
        with self._chat_state_lock:
            if account_id in self._active_chat_accounts:
                raise GatewayRequestError(
                    "WORKSPACE_RUN_IN_PROGRESS",
                    "This Workspace already has an active agent run.",
                )
            self._active_chat_accounts.add(account_id)

    def _release_chat_account(self, account: dict) -> None:
        account_id = _safe_text(account.get("account_id"), 128)
        if not account_id:
            return
        with self._chat_state_lock:
            self._active_chat_accounts.discard(account_id)

    def _gateway(self) -> OpenClawGatewayClient:
        if self.gateway is None:
            raise GatewayRequestError(
                "GATEWAY_NOT_CONFIGURED",
                "OpenClaw Gateway is not configured.",
            )
        return self.gateway


def _validated_chat_attachments(values: list[dict] | None) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_CHAT_ATTACHMENTS:
        raise ValueError("chat attachments must contain at most 4 images")
    normalized = []
    total_bytes = 0
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError("chat attachment is invalid")
        mime_type = str(value.get("mimeType") or "").strip().lower()
        signature = _CHAT_IMAGE_TYPES.get(mime_type)
        if signature is None:
            raise ValueError("chat attachment type is unsupported")
        content = str(value.get("content") or "").strip()
        if not content or content.startswith("data:"):
            raise ValueError("chat attachment content is invalid")
        try:
            body = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("chat attachment content is invalid") from exc
        if not body or len(body) > MAX_CHAT_ATTACHMENT_BYTES:
            raise ValueError("chat attachment exceeds the per-image size limit")
        if mime_type == "image/webp":
            magic_matches = (
                body.startswith(signature[0])
                and len(body) >= 12
                and body[8:12] == b"WEBP"
            )
        else:
            magic_matches = body.startswith(signature[0])
        if not magic_matches:
            raise ValueError("chat attachment content does not match its image type")
        total_bytes += len(body)
        if total_bytes > MAX_CHAT_ATTACHMENTS_TOTAL_BYTES:
            raise ValueError("chat attachments exceed the total size limit")
        supplied_name = str(value.get("fileName") or "").replace("\\", "/")
        supplied_name = supplied_name.rsplit("/", 1)[-1].strip()
        if not supplied_name or any(ord(character) < 32 for character in supplied_name):
            supplied_name = f"image-{index + 1}{signature[1]}"
        normalized.append(
            {
                "type": "image",
                "mimeType": mime_type,
                "fileName": supplied_name[:120],
                "content": content,
            }
        )
    return normalized


def _workspace_gateway_failure_text(error: GatewayRequestError) -> str:
    code = str(error.code or "GATEWAY_REQUEST_FAILED")
    details = error.details if isinstance(error.details, dict) else {}
    if code == "WORKSPACE_RUN_IN_PROGRESS":
        return "\u4e0a\u4e00\u6761\u7f51\u9875\u4efb\u52a1\u4ecd\u5728\u5904\u7406\uff0c\u672c\u6b21\u8bf7\u6c42\u6ca1\u6709\u6392\u961f\u3002"
    if code == "GATEWAY_RUN_TIMEOUT_ABORTED":
        if details.get("hadToolActivity") is True:
            return "\u667a\u80fd\u4f53\u8fd0\u884c\u8d85\u65f6\uff0c\u5df2\u505c\u6b62\u540e\u7eed\u5904\u7406\uff1b\u5982\u6d89\u53ca\u5199\u64cd\u4f5c\uff0c\u8bf7\u5148\u6838\u5bf9\u4e1a\u52a1\u7cfb\u7edf\u7ed3\u679c\u3002"
        return "\u667a\u80fd\u4f53\u8fd0\u884c\u8d85\u65f6\uff0c\u5df2\u5b89\u5168\u4e2d\u6b62\uff1b\u672c\u6b21\u5c1a\u672a\u8c03\u7528\u4e1a\u52a1\u7cfb\u7edf\u3002"
    if code == "GATEWAY_RUN_TIMEOUT_ABORT_UNCONFIRMED":
        return "OpenClaw \u6682\u65f6\u65e0\u54cd\u5e94\uff0c\u672c\u6b21\u8bf7\u6c42\u5df2\u505c\u6b62\u7ee7\u7eed\u6392\u961f\uff1b\u5982\u6d89\u53ca\u5199\u64cd\u4f5c\uff0c\u8bf7\u5148\u6838\u5bf9\u4e1a\u52a1\u7cfb\u7edf\u7ed3\u679c\u3002"
    if code == "GATEWAY_SESSION_NOT_IDLE":
        return "\u4e0a\u4e00\u6761\u667a\u80fd\u4f53\u4efb\u52a1\u672a\u80fd\u53ca\u65f6\u7ed3\u675f\uff0c\u672c\u6b21\u8bf7\u6c42\u672a\u8fdb\u5165\u4e1a\u52a1\u7cfb\u7edf\u3002"
    if code == "GATEWAY_SESSION_STATE_UNAVAILABLE":
        return "\u6682\u65f6\u65e0\u6cd5\u786e\u8ba4\u667a\u80fd\u4f53\u4f1a\u8bdd\u662f\u5426\u7a7a\u95f2\uff0c\u672c\u6b21\u8bf7\u6c42\u672a\u8fdb\u5165\u4e1a\u52a1\u7cfb\u7edf\u3002"
    if code == "GATEWAY_START_STALLED_ABORTED":
        return "\u667a\u80fd\u4f53\u81ea\u52a8\u6062\u590d\u540e\u4ecd\u672a\u80fd\u542f\u52a8\uff0c\u5df2\u5b89\u5168\u4e2d\u6b62\uff1b\u672c\u6b21\u5c1a\u672a\u8c03\u7528\u4e1a\u52a1\u7cfb\u7edf\u3002"
    if code == "GATEWAY_START_STALLED_ABORT_UNCONFIRMED":
        return "\u667a\u80fd\u4f53\u542f\u52a8\u72b6\u6001\u65e0\u6cd5\u786e\u8ba4\uff0c\u5df2\u505c\u6b62\u81ea\u52a8\u6062\u590d\uff1b\u8bf7\u7a0d\u540e\u67e5\u8be2\u4efb\u52a1\u72b6\u6001\u3002"
    if code == "GATEWAY_START_RECOVERY_BLOCKED_TOOL_ACTIVITY":
        return "\u667a\u80fd\u4f53\u542f\u52a8\u5f02\u5e38\uff0c\u4f46\u68c0\u6d4b\u5230\u672c\u8f6e\u5df2\u5c1d\u8bd5\u8c03\u7528\u4e1a\u52a1\u5de5\u5177\uff1b\u5df2\u505c\u6b62\u81ea\u52a8\u91cd\u653e\uff0c\u8bf7\u5148\u6838\u5bf9\u4e1a\u52a1\u7cfb\u7edf\u72b6\u6001\u3002"
    if code == "GATEWAY_START_RECOVERY_EVIDENCE_UNAVAILABLE":
        return "\u667a\u80fd\u4f53\u542f\u52a8\u5f02\u5e38\uff0c\u4e14\u65e0\u6cd5\u786e\u8ba4\u672c\u8f6e\u662f\u5426\u5df2\u8c03\u7528\u4e1a\u52a1\u5de5\u5177\uff1b\u5df2\u505c\u6b62\u81ea\u52a8\u91cd\u653e\u3002"
    if code.startswith("GATEWAY_"):
        if details.get("hadToolActivity") is True:
            return (
                "OpenClaw \u5b9e\u65f6\u8fde\u63a5\u5df2\u4e2d\u65ad\uff1b\u672c\u6b21\u5df2\u8c03\u7528\u4e1a\u52a1\u80fd\u529b\uff0c"
                "\u4f46\u672a\u80fd\u53d6\u5f97\u6700\u7ec8\u7b54\u590d\u3002AgentBridge \u4e0d\u4f1a\u81ea\u52a8\u91cd\u653e\uff1b"
                "\u8bf7\u5237\u65b0\u67e5\u770b\u5df2\u843d\u76d8\u7ed3\u679c\uff0c\u6d89\u53ca\u5199\u64cd\u4f5c\u65f6\u8bf7\u5148\u6838\u5bf9\u4e1a\u52a1\u7cfb\u7edf\u3002"
            )
        return "OpenClaw \u6682\u65f6\u65e0\u54cd\u5e94\uff0c\u672c\u6b21\u8bf7\u6c42\u672a\u7ee7\u7eed\u8fdb\u5165\u4e1a\u52a1\u7cfb\u7edf\u3002"
    return f"\u667a\u80fd\u4f53\u672a\u80fd\u5b8c\u6210\u672c\u6b21\u8bf7\u6c42\uff08\u9519\u8bef\u7801\uff1a{code}\uff09\u3002"


def _terminal_chat_failure_text(item: dict[str, Any]) -> str:
    supplied = _safe_text(item.get("text"), 2_000)
    if supplied:
        return supplied
    if item.get("state") == "aborted":
        if item.get("hadToolActivity") is True:
            return "\u667a\u80fd\u4f53\u8fd0\u884c\u5df2\u505c\u6b62\uff1b\u4efb\u52a1\u5df2\u7ecf\u8c03\u7528\u4e1a\u52a1\u5de5\u5177\uff0c\u8bf7\u5148\u6838\u5bf9\u4e1a\u52a1\u7cfb\u7edf\u72b6\u6001\u3002"
        return "\u667a\u80fd\u4f53\u8fd0\u884c\u5df2\u505c\u6b62\uff0c\u5c1a\u672a\u8c03\u7528\u4e1a\u52a1\u5de5\u5177\uff0c\u53ef\u4ee5\u5b89\u5168\u5730\u91cd\u65b0\u53d1\u9001\u3002"
    return "\u667a\u80fd\u4f53\u672a\u80fd\u5b8c\u6210\u672c\u6b21\u8bf7\u6c42\u3002"


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


def _public_artifact(artifact: dict) -> dict:
    state = artifact.get("state")
    return {
        "artifact_id": artifact.get("artifact_id"),
        "task_id": artifact.get("task_id"),
        "artifact_type": artifact.get("artifact_type"),
        "filename": artifact.get("filename"),
        "content_type": artifact.get("content_type"),
        "byte_size": artifact.get("byte_size"),
        "download_url": (
            artifact.get("download_url") if state == "ready" else None
        ),
        "state": state,
        "created_at": artifact.get("created_at"),
        "expires_at": artifact.get("expires_at"),
    }


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
    dedupe_key = str(entry.get("dedupe_key") or "")
    message_key = (
        dedupe_key.removeprefix("message:")
        if entry.get("entry_type") == "chat_message"
        and dedupe_key.startswith("message:")
        else None
    )
    return {
        "entry_id": entry["entry_id"],
        "sequence": entry["sequence"],
        "entry_type": entry["entry_type"],
        "message_key": message_key,
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
                    _visible_timestamp(
                        value.get("timestamp")
                        or value.get("createdAt")
                        or value.get("created_at")
                    ),
                    80,
                ),
            }
        )
    return messages


def _visible_timestamp(value: Any) -> str | None:
    text = _safe_text(value, 80)
    if not text:
        return None
    if text.isdigit():
        numeric = int(text)
        if 1_000_000_000 <= numeric < 10_000_000_000:
            seconds = float(numeric)
        elif 1_000_000_000_000 <= numeric < 10_000_000_000_000:
            seconds = numeric / 1_000
        else:
            return text
        try:
            return datetime.fromtimestamp(
                seconds,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds")
        except (OverflowError, OSError, ValueError):
            return text
    return text


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
