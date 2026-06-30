from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, UTC, timedelta
import threading
import uuid
from typing import Any

CLIENT_TTL_SECONDS = 120


@dataclass
class ExtensionClient:
    client_id: str
    tab_id: int
    url: str
    title: str
    registered_at: str
    extension_version: str = ""


@dataclass
class ExtensionTask:
    id: str
    system: str
    kind: str
    payload: dict[str, Any]
    created_at: str
    target_client_id: str | None = None


class ExtensionBridge:
    def __init__(self) -> None:
        self.clients: dict[str, ExtensionClient] = {}
        self.tasks: dict[str, ExtensionTask] = {}
        self.task_claims: dict[str, dict[str, Any]] = {}
        self.pending_tasks: list[ExtensionTask] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.task_events: dict[str, list[dict[str, Any]]] = {}
        self._condition = threading.Condition()

    def register_client(
        self,
        client_id: str,
        *,
        tab_id: int,
        url: str,
        title: str,
        extension_version: str = "",
    ) -> None:
        with self._condition:
            self.clients[client_id] = ExtensionClient(
                client_id=client_id,
                tab_id=tab_id,
                url=url,
                title=title,
                registered_at=self._now(),
                extension_version=extension_version,
            )

    def enqueue_task(
        self,
        *,
        system: str,
        kind: str,
        payload: dict[str, Any],
        target_client_id: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        with self._condition:
            task = ExtensionTask(
                id=task_id,
                system=system,
                kind=kind,
                payload=payload,
                created_at=self._now(),
                target_client_id=target_client_id,
            )
            self.tasks[task_id] = task
            self.pending_tasks.append(
                task
            )
            self._condition.notify_all()
        return task_id

    def poll_tasks(self, client_id: str) -> list[dict[str, Any]]:
        with self._condition:
            self._prune_stale_clients()
            if client_id not in self.clients:
                raise KeyError(f"extension client not registered: {client_id}")
            claimable = [
                task
                for task in self.pending_tasks
                if task.target_client_id is None or task.target_client_id == client_id
            ]
            claimed_ids = {task.id for task in claimable}
            claimed_at = self._now()
            for task in claimable:
                self.task_claims[task.id] = {
                    "claimed": True,
                    "claimed_by": client_id,
                    "claimed_at": claimed_at,
                }
            self.pending_tasks = [
                task for task in self.pending_tasks if task.id not in claimed_ids
            ]
            tasks = [asdict(task) for task in claimable]
        return tasks

    def submit_result(
        self,
        *,
        client_id: str,
        task_id: str,
        ok: bool,
        result: Any | None = None,
        error: str | None = None,
    ) -> None:
        with self._condition:
            self._prune_stale_clients()
            if client_id not in self.clients:
                raise KeyError(f"extension client not registered: {client_id}")
            self.results[task_id] = {
                "client_id": client_id,
                "task_id": task_id,
                "ok": ok,
                "result": result,
                "error": error,
                "finished_at": self._now(),
            }
            self._condition.notify_all()

    def submit_event(
        self,
        *,
        client_id: str,
        task_id: str,
        stage: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._condition:
            self._prune_stale_clients()
            if client_id not in self.clients:
                raise KeyError(f"extension client not registered: {client_id}")
            self.task_events.setdefault(task_id, []).append(
                {
                    "client_id": client_id,
                    "task_id": task_id,
                    "stage": stage,
                    "detail": detail or {},
                    "created_at": self._now(),
                }
            )
            self._condition.notify_all()

    def get_result(self, task_id: str) -> dict[str, Any]:
        if task_id not in self.results:
            raise KeyError(f"task result not found: {task_id}")
        return self.results[task_id]

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        return list(self.task_events.get(task_id, []))

    def get_task_state(self, task_id: str) -> dict[str, Any]:
        with self._condition:
            task = self.tasks.get(task_id)
            pending = any(item.id == task_id for item in self.pending_tasks)
            claim = self.task_claims.get(task_id, {})
            return {
                "task_id": task_id,
                "known": task is not None,
                "system": task.system if task else "",
                "kind": task.kind if task else "",
                "target_client_id": task.target_client_id if task else None,
                "created_at": task.created_at if task else "",
                "pending": pending,
                "claimed": bool(claim.get("claimed")),
                "claimed_by": claim.get("claimed_by", ""),
                "claimed_at": claim.get("claimed_at", ""),
                "event_count": len(self.task_events.get(task_id, [])),
                "has_result": task_id in self.results,
            }

    def list_clients(self) -> list[dict[str, Any]]:
        with self._condition:
            self._prune_stale_clients()
            return [
                asdict(client)
                for client in sorted(
                    self.clients.values(),
                    key=lambda client: client.registered_at,
                    reverse=True,
                )
            ]

    def wait_for_result(self, task_id: str, *, timeout_seconds: float) -> dict[str, Any] | None:
        with self._condition:
            if task_id not in self.results:
                self._condition.wait_for(
                    lambda: task_id in self.results,
                    timeout=timeout_seconds,
                )
            return self.results.get(task_id)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _prune_stale_clients(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=CLIENT_TTL_SECONDS)
        stale_ids = [
            client_id
            for client_id, client in self.clients.items()
            if self._parse_time(client.registered_at) < cutoff
        ]
        for client_id in stale_ids:
            del self.clients[client_id]

    def _parse_time(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
