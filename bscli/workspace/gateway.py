from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any, Iterator


_LOG = logging.getLogger(__name__)

_PRE_ACCEPT_RETRY_CODES = {
    "GATEWAY_CONNECTION_CLOSED",
    "GATEWAY_CONNECTION_FAILED",
    "GATEWAY_PROCESS_FAILED",
    "GATEWAY_RESPONSE_INVALID",
    "GATEWAY_TIMEOUT",
}
_PRE_ACCEPT_RETRY_STAGES = {"connect", "preflight_abort", "bind"}


class GatewayRequestError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class OpenClawGatewayClient:
    def __init__(
        self,
        *,
        url: str,
        token_file: Path | str,
        state_dir: Path | str,
        node_executable: str = "node",
        script_path: Path | str | None = None,
    ) -> None:
        self.url = str(url or "").strip()
        self.token_file = Path(token_file)
        self.state_dir = Path(state_dir)
        self.node_executable = node_executable
        self.script_path = Path(script_path or Path(__file__).with_name(
            "gateway_client.mjs"
        ))
        if not self.url.startswith(("ws://", "wss://")):
            raise ValueError("OpenClaw Gateway URL must use ws:// or wss://")

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 30,
    ) -> Any:
        environment = self._environment()
        payload = {
            "method": str(method),
            "params": params or {},
            "timeoutMs": min(max(int(timeout_seconds * 1000), 1_000), 180_000),
        }
        try:
            completed = subprocess.run(
                [
                    self.node_executable,
                    "--experimental-websocket",
                    str(self.script_path),
                ],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds + 5,
                env=environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GatewayRequestError(
                "GATEWAY_PROCESS_FAILED",
                "OpenClaw Gateway client process failed.",
            ) from exc
        try:
            result = json.loads(completed.stdout.strip())
        except (json.JSONDecodeError, AttributeError) as exc:
            raise GatewayRequestError(
                "GATEWAY_RESPONSE_INVALID",
                "OpenClaw Gateway returned an invalid response.",
            ) from exc
        if not result.get("ok"):
            error = result.get("error") if isinstance(
                result.get("error"), dict
            ) else {}
            raise GatewayRequestError(
                str(error.get("code") or "GATEWAY_REQUEST_FAILED"),
                str(error.get("message") or "OpenClaw Gateway request failed."),
                error.get("details") if isinstance(
                    error.get("details"), dict
                ) else None,
            )
        return result.get("payload")

    def stream(
        self,
        *,
        session_key: str,
        timeout_seconds: float = 30,
    ) -> Iterator[dict[str, Any]]:
        session_key = str(session_key or "").strip()
        if len(session_key) < 16 or len(session_key) > 1_024:
            raise ValueError("OpenClaw session key is invalid")
        payload = {
            "mode": "stream",
            "sessionKey": session_key,
            "timeoutMs": min(
                max(int(timeout_seconds * 1000), 1_000),
                180_000,
            ),
        }
        return self._stream_payload(payload)

    def send_stream(
        self,
        *,
        session_key: str,
        endpoint_key: str,
        grant: str,
        message: str,
        idempotency_key: str,
        timeout_seconds: float = 150,
    ) -> Iterator[dict[str, Any]]:
        values = {
            "sessionKey": (session_key, 16, 1_024),
            "endpointKey": (endpoint_key, 1, 768),
            "grant": (grant, 32, 256),
            "message": (message, 1, 20_000),
            "idempotencyKey": (idempotency_key, 1, 128),
        }
        normalized: dict[str, str] = {}
        for name, (value, minimum, maximum) in values.items():
            text = str(value or "").strip()
            if len(text) < minimum or len(text) > maximum:
                raise ValueError(f"OpenClaw {name} is invalid")
            normalized[name] = text
        payload = {
            "mode": "send-stream",
            **normalized,
            "preflightAbort": True,
            "acceptTimeoutMs": 35_000,
            "startupProgressTimeoutMs": 15_000,
            "sessionIdleTimeoutMs": 15_000,
            "sessionIdlePollMs": 250,
            "timeoutMs": min(
                max(int(timeout_seconds * 1000), 1_000),
                180_000,
            ),
        }

        def guarded_stream() -> Iterator[dict[str, Any]]:
            run_id: str | None = None
            terminal = False
            source = None
            try:
                for attempt in range(2):
                    accepted = False
                    source = self._stream_payload(payload)
                    try:
                        for item in source:
                            if item.get("type") == "accepted":
                                accepted = True
                                run_id = (
                                    str(item.get("runId") or "").strip()
                                    or None
                                )
                            if (
                                item.get("type") == "chat"
                                and item.get("state")
                                in {"final", "error", "aborted"}
                            ):
                                terminal = True
                            yield item
                        return
                    except GatewayRequestError as exc:
                        if not _should_retry_before_accept(
                            exc,
                            accepted=accepted,
                            attempt=attempt,
                        ):
                            raise
                        _LOG.warning(
                            "Retrying OpenClaw Gateway before run acceptance "
                            "code=%s stage=%s attempt=%s",
                            exc.code,
                            str(exc.details.get("stage") or "unknown"),
                            attempt + 1,
                        )
                        yield {
                            "type": "progress",
                            "runId": normalized["idempotencyKey"],
                            "kind": "system",
                            "phase": "retry",
                            "label": "OpenClaw connection is recovering",
                        }
                    finally:
                        close = getattr(source, "close", None)
                        if callable(close):
                            close()
                        source = None
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    close()
                if run_id and not terminal:
                    self.abort_chat(
                        session_key=normalized["sessionKey"],
                        run_id=run_id,
                        timeout_seconds=5,
                        raise_on_error=False,
                    )

        return guarded_stream()

    def abort_chat(
        self,
        *,
        session_key: str,
        run_id: str | None = None,
        preserve_side_runs: bool = True,
        timeout_seconds: float = 8,
        raise_on_error: bool = True,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {
            "sessionKey": str(session_key or "").strip(),
        }
        if not params["sessionKey"]:
            raise ValueError("OpenClaw session key is invalid")
        if run_id:
            params["runId"] = str(run_id).strip()
        elif preserve_side_runs:
            params["preserveSideRuns"] = True
        try:
            result = self.call(
                "chat.abort",
                params,
                timeout_seconds=timeout_seconds,
            )
        except GatewayRequestError as exc:
            if (
                not run_id
                and preserve_side_runs
                and exc.code == "INVALID_REQUEST"
            ):
                params.pop("preserveSideRuns", None)
                try:
                    result = self.call(
                        "chat.abort",
                        params,
                        timeout_seconds=timeout_seconds,
                    )
                except GatewayRequestError:
                    if raise_on_error:
                        raise
                    return None
                return result if isinstance(result, dict) else {}
            if raise_on_error:
                raise
            return None
        return result if isinstance(result, dict) else {}

    def _stream_payload(
        self,
        payload: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        try:
            process = subprocess.Popen(
                [
                    self.node_executable,
                    "--experimental-websocket",
                    str(self.script_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=self._environment(),
            )
        except OSError as exc:
            raise GatewayRequestError(
                "GATEWAY_PROCESS_FAILED",
                "OpenClaw Gateway stream process failed.",
            ) from exc
        try:
            if process.stdin is None or process.stdout is None:
                raise GatewayRequestError(
                    "GATEWAY_PROCESS_FAILED",
                    "OpenClaw Gateway stream process is unavailable.",
                )
            process.stdin.write(json.dumps(payload, ensure_ascii=False))
            process.stdin.close()
            saw_eof = False
            for raw_line in process.stdout:
                try:
                    item = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "eof":
                    saw_eof = True
                    break
                if item.get("type") == "ready":
                    continue
                if item.get("type") == "error":
                    error = (
                        item.get("error")
                        if isinstance(item.get("error"), dict)
                        else {}
                    )
                    raise GatewayRequestError(
                        str(error.get("code") or "GATEWAY_STREAM_FAILED"),
                        str(
                            error.get("message")
                            or "OpenClaw Gateway stream failed."
                        ),
                        error.get("details") if isinstance(
                            error.get("details"), dict
                        ) else None,
                    )
                if item.get("type") in {"accepted", "progress", "chat"}:
                    yield item
            if not saw_eof:
                raise GatewayRequestError(
                    "GATEWAY_RESPONSE_INVALID",
                    "OpenClaw Gateway stream ended without a terminal frame.",
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def _environment(self) -> dict[str, str]:
        token = self._read_token()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "AB_GATEWAY_URL": self.url,
                "AB_GATEWAY_TOKEN": token,
                "AB_GATEWAY_IDENTITY_PATH": str(
                    self.state_dir / "device-identity.json"
                ),
            }
        )
        return environment

    def _read_token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise GatewayRequestError(
                "GATEWAY_TOKEN_UNAVAILABLE",
                "OpenClaw Gateway credentials are unavailable.",
            ) from exc
        if len(token) < 16:
            raise GatewayRequestError(
                "GATEWAY_TOKEN_INVALID",
                "OpenClaw Gateway credentials are invalid.",
            )
        return token


def _should_retry_before_accept(
    error: GatewayRequestError,
    *,
    accepted: bool,
    attempt: int,
) -> bool:
    if accepted or attempt >= 1 or error.code not in _PRE_ACCEPT_RETRY_CODES:
        return False
    stage = str(error.details.get("stage") or "").strip()
    if stage:
        return stage in _PRE_ACCEPT_RETRY_STAGES
    return error.code in {"GATEWAY_PROCESS_FAILED", "GATEWAY_RESPONSE_INVALID"}
