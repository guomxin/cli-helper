from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
import hmac
import logging
from queue import Empty, Queue
import secrets
import threading
import time
from typing import Any, Callable

from bscli.adapters.base import (
    AdapterLoginRequired,
    AdapterSessionCheckUnavailable,
)
from bscli.core.auth_challenges import AuthChallengeStore
from bscli.core.session_secrets import SessionSecretError, SessionStateStore
from bscli.core.sessions import SessionPrincipalMismatch, SessionRegistry


logger = logging.getLogger(__name__)

_ALLOWED_KEYS = {
    "Backspace",
    "Delete",
    "Enter",
    "Escape",
    "Tab",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "ArrowUp",
}


class InteractiveBrowserAccessDenied(RuntimeError):
    pass


class InteractiveBrowserUnavailable(RuntimeError):
    pass


class InteractiveBrowserBroker:
    """Own short-lived browser views used only by trusted authentication cards."""

    def __init__(
        self,
        *,
        challenge_store: AuthChallengeStore,
        session_registry: SessionRegistry,
        session_state_store: SessionStateStore,
        adapter_factory: Callable[[dict], object],
        worker_factory: Callable[[dict, object], object],
        login_timeout_seconds: float = 900,
    ) -> None:
        if login_timeout_seconds < 30 or login_timeout_seconds > 900:
            raise ValueError("interactive login timeout must be between 30 and 900 seconds")
        self.challenge_store = challenge_store
        self.session_registry = session_registry
        self.session_state_store = session_state_store
        self.adapter_factory = adapter_factory
        self.worker_factory = worker_factory
        self.login_timeout_seconds = login_timeout_seconds
        self._lock = threading.Lock()
        self._runs: dict[str, _InteractiveRun] = {}

    def start(
        self,
        *,
        challenge_id: str,
        csrf_token: str,
        csrf_cookie: str,
    ) -> dict:
        challenge = self.challenge_store.claim(
            challenge_id,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        session = self.session_registry.get(challenge["session_id"])
        adapter = self.adapter_factory(challenge)
        contract = adapter.authentication_contract()
        try:
            self._validate_bindings(challenge, session, contract)
            self.session_state_store.delete(session["session_id"])
            self.session_registry.mark_awaiting_login(session["session_id"])
            run = _InteractiveRun(
                challenge=challenge,
                session=session,
                contract=contract,
                adapter=adapter,
                worker_factory=self.worker_factory,
                challenge_store=self.challenge_store,
                session_registry=self.session_registry,
                session_state_store=self.session_state_store,
                timeout_seconds=min(
                    self.login_timeout_seconds,
                    _remaining_challenge_seconds(challenge),
                ),
            )
            with self._lock:
                existing = self._runs.get(challenge_id)
                if existing is not None and not existing.done:
                    raise RuntimeError("interactive browser login is already running")
                self._runs[challenge_id] = run
            run.start()
            if not run.wait_until_ready(timeout_seconds=20):
                if run.done:
                    return self.status(challenge_id=challenge_id)
                run.stop()
                return self._fail(
                    challenge_id,
                    session,
                    code="INTERACTIVE_BROWSER_START_TIMEOUT",
                    message="The trusted interactive browser did not start in time.",
                )
            return {
                "status": "processing",
                "challengeId": challenge_id,
                "controlToken": run.control_token,
                "viewport": run.viewport,
            }
        except Exception:
            logger.exception(
                "Interactive browser start failed for challenge %s",
                challenge_id,
            )
            return self._fail(
                challenge_id,
                session,
                code="INTERACTIVE_BROWSER_START_FAILED",
                message="The trusted interactive browser could not be started.",
            )

    def frame(self, *, challenge_id: str, control_token: str) -> bytes:
        run = self._authorized_run(challenge_id, control_token)
        return run.command("screenshot", {}, timeout_seconds=15)

    def send_event(
        self,
        *,
        challenge_id: str,
        control_token: str,
        event: dict,
    ) -> dict:
        run = self._authorized_run(challenge_id, control_token)
        kind = str(event.get("type") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        run.command(kind, payload, timeout_seconds=10)
        return {"status": "accepted", "challengeId": challenge_id}

    def status(
        self,
        *,
        challenge_id: str,
        control_token: str | None = None,
    ) -> dict:
        if control_token is not None:
            self._authorized_run(challenge_id, control_token, allow_done=True)
        challenge = self.challenge_store.get(challenge_id)
        return {
            "status": challenge["state"],
            "challengeId": challenge_id,
            "error": challenge.get("error"),
            "result": challenge.get("result"),
        }

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop()
        for run in runs:
            run.join(timeout_seconds=5)

    def _authorized_run(
        self,
        challenge_id: str,
        control_token: str,
        *,
        allow_done: bool = False,
    ) -> _InteractiveRun:
        with self._lock:
            run = self._runs.get(challenge_id)
        if run is None or (run.done and not allow_done):
            raise InteractiveBrowserUnavailable(
                "interactive browser login is not available"
            )
        if not hmac.compare_digest(run.control_token, str(control_token or "")):
            raise InteractiveBrowserAccessDenied(
                "interactive browser control token is invalid"
            )
        return run

    def _fail(
        self,
        challenge_id: str,
        session: dict,
        *,
        code: str,
        message: str,
    ) -> dict:
        try:
            self.session_registry.mark_expired(session["session_id"], message)
        except Exception:
            pass
        try:
            self.session_state_store.delete(session["session_id"])
        except SessionSecretError:
            pass
        try:
            challenge = self.challenge_store.fail(
                challenge_id,
                code=code,
                message=message,
            )
        except Exception:
            challenge = self.challenge_store.get(challenge_id)
        return {
            "status": "failed",
            "challengeId": challenge_id,
            "error": challenge.get("error") or {"code": code, "message": message},
        }

    @staticmethod
    def _validate_bindings(challenge: dict, session: dict, contract: dict) -> None:
        if challenge["challenge_type"] != "interactive_browser_login":
            raise ValueError("challenge is not an interactive browser login")
        if contract.get("authentication_mode") != "interactive_browser":
            raise ValueError("adapter does not declare interactive browser login")
        if any(
            (
                session["session_id"] != challenge["session_id"],
                session["user_subject"] != challenge["user_subject"],
                session["system_id"] != challenge["system_id"],
                contract.get("system_id") != challenge["system_id"],
                contract.get("origin") != challenge["origin"],
                contract.get("page_fingerprint") != challenge["page_fingerprint"],
                contract.get("fields") != challenge["fields"],
            )
        ):
            raise ValueError("interactive authentication binding mismatch")


class _InteractiveRun:
    def __init__(
        self,
        *,
        challenge: dict,
        session: dict,
        contract: dict,
        adapter: object,
        worker_factory: Callable[[dict, object], object],
        challenge_store: AuthChallengeStore,
        session_registry: SessionRegistry,
        session_state_store: SessionStateStore,
        timeout_seconds: float,
    ) -> None:
        self.challenge = challenge
        self.session = session
        self.contract = contract
        self.adapter = adapter
        self.worker_factory = worker_factory
        self.challenge_store = challenge_store
        self.session_registry = session_registry
        self.session_state_store = session_state_store
        self.timeout_seconds = timeout_seconds
        self.control_token = secrets.token_urlsafe(32)
        viewport = contract.get("interactive", {}).get("viewport") or {}
        self.viewport = {
            "width": _bounded_coordinate(viewport.get("width"), default=430, maximum=1600),
            "height": _bounded_coordinate(viewport.get("height"), default=760, maximum=1200),
        }
        self._queue: Queue[tuple[str, dict, Future] | None] = Queue(maxsize=100)
        self._ready = threading.Event()
        self._done = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agentbridge-interactive-login-{challenge['challenge_id'][:10]}",
            daemon=True,
        )

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def join(self, *, timeout_seconds: float) -> None:
        self._thread.join(timeout=max(timeout_seconds, 0))

    def wait_until_ready(self, *, timeout_seconds: float) -> bool:
        return self._ready.wait(timeout=max(timeout_seconds, 0))

    def command(
        self,
        kind: str,
        payload: dict,
        *,
        timeout_seconds: float,
    ):
        if self.done:
            raise InteractiveBrowserUnavailable("interactive browser login has ended")
        future: Future = Future()
        try:
            self._queue.put((kind, payload, future), timeout=2)
        except Exception as exc:
            raise InteractiveBrowserUnavailable(
                "interactive browser command queue is unavailable"
            ) from exc
        try:
            return future.result(timeout=max(timeout_seconds, 0.1))
        except TimeoutError as exc:
            raise InteractiveBrowserUnavailable(
                "interactive browser command timed out"
            ) from exc

    def _run(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        last_probe = 0.0
        try:
            with self.worker_factory(self.session, self.adapter) as worker:
                worker.clear_session_state()
                self.adapter.begin_interactive_login(
                    worker,
                    timeout_seconds=min(45, self.timeout_seconds),
                )
                page = worker.page
                set_viewport = getattr(page, "set_viewport_size", None)
                if callable(set_viewport):
                    set_viewport(self.viewport)
                self._ready.set()
                while not self._stop.is_set() and time.monotonic() < deadline:
                    try:
                        queued = self._queue.get(timeout=0.15)
                    except Empty:
                        queued = None
                    if queued is not None:
                        kind, payload, future = queued
                        try:
                            future.set_result(self._execute(page, kind, payload))
                        except Exception as exc:
                            future.set_exception(exc)
                    now = time.monotonic()
                    if now - last_probe < 1:
                        continue
                    last_probe = now
                    try:
                        probe = self.adapter.probe_session(worker)
                    except (AdapterLoginRequired, AdapterSessionCheckUnavailable):
                        continue
                    active = self.session_registry.activate(
                        self.session["session_id"],
                        observed_principal_ref=probe.get("observed_principal_ref"),
                    )
                    self.session_state_store.save(
                        self.session["session_id"],
                        worker.capture_session_state(),
                    )
                    self.challenge_store.complete(
                        self.challenge["challenge_id"],
                        result={
                            "session_id": active["session_id"],
                            "observed_principal_ref": active[
                                "downstream_principal_ref"
                            ],
                            "template_count": None,
                        },
                    )
                    return
                if self._stop.is_set():
                    self._finish_failed(
                        "INTERACTIVE_BROWSER_CANCELLED",
                        "The trusted interactive browser login was cancelled.",
                    )
                else:
                    self._finish_failed(
                        "INTERACTIVE_BROWSER_TIMEOUT",
                        "The trusted interactive browser login timed out.",
                    )
        except SessionPrincipalMismatch:
            self._finish_failed(
                "PRINCIPAL_MISMATCH",
                "The authenticated Yuque identity did not match the expected identity.",
                quarantine=True,
            )
        except Exception:
            logger.exception(
                "Interactive browser failed for challenge %s",
                self.challenge["challenge_id"],
            )
            self._finish_failed(
                "INTERACTIVE_BROWSER_FAILED",
                "The trusted interactive browser login failed.",
            )
        finally:
            self._ready.set()
            self._done.set()
            while True:
                try:
                    queued = self._queue.get_nowait()
                except Empty:
                    break
                if queued is not None:
                    _kind, _payload, future = queued
                    if not future.done():
                        future.set_exception(
                            InteractiveBrowserUnavailable(
                                "interactive browser login has ended"
                            )
                        )

    def _finish_failed(
        self,
        code: str,
        message: str,
        *,
        quarantine: bool = False,
    ) -> None:
        try:
            if quarantine:
                self.session_registry.quarantine(self.session["session_id"], message)
            else:
                self.session_registry.mark_expired(self.session["session_id"], message)
        except Exception:
            pass
        try:
            self.session_state_store.delete(self.session["session_id"])
        except SessionSecretError:
            pass
        try:
            self.challenge_store.fail(
                self.challenge["challenge_id"],
                code=code,
                message=message,
            )
        except Exception:
            pass

    def _execute(self, page, kind: str, payload: dict):
        if kind == "screenshot":
            return page.screenshot(type="png")
        if kind == "pointer_gesture":
            points = _validated_pointer_gesture(payload, viewport=self.viewport)
            duration_ms = points[-1]["t"] - points[0]["t"]
            logger.info(
                "Replaying trusted browser pointer gesture for challenge %s: "
                "points=%s duration_ms=%s",
                self.challenge["challenge_id"],
                len(points),
                duration_ms,
            )
            first = points[0]
            page.mouse.move(first["x"], first["y"])
            page.mouse.down()
            try:
                previous_t = first["t"]
                for point in points[1:]:
                    delay_seconds = (point["t"] - previous_t) / 1000
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    page.mouse.move(point["x"], point["y"])
                    previous_t = point["t"]
            finally:
                page.mouse.up()
            return None
        if kind in {"pointer_down", "pointer_move", "pointer_up", "click"}:
            x = _bounded_coordinate(payload.get("x"), default=0, maximum=self.viewport["width"])
            y = _bounded_coordinate(payload.get("y"), default=0, maximum=self.viewport["height"])
            page.mouse.move(x, y)
            if kind == "pointer_down":
                page.mouse.down()
            elif kind == "pointer_up":
                page.mouse.up()
            elif kind == "click":
                page.mouse.click(x, y)
            return None
        if kind == "type_text":
            value = str(payload.get("text") or "")
            if not value or len(value) > 2048:
                raise ValueError("interactive browser text is invalid")
            page.keyboard.insert_text(value)
            return None
        if kind == "key":
            key = str(payload.get("key") or "")
            if key not in _ALLOWED_KEYS:
                raise ValueError("interactive browser key is not allowed")
            page.keyboard.press(key)
            return None
        if kind == "wheel":
            delta_y = max(-2000, min(2000, int(payload.get("deltaY") or 0)))
            page.mouse.wheel(0, delta_y)
            return None
        raise ValueError(f"unsupported interactive browser event: {kind}")


def _bounded_coordinate(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    parsed = int(round(float(value)))
    return max(0, min(maximum, parsed))


def _validated_pointer_gesture(
    payload: dict,
    *,
    viewport: dict,
) -> list[dict[str, int]]:
    points = payload.get("points")
    if not isinstance(points, list) or not 1 <= len(points) <= 240:
        raise ValueError("interactive browser pointer gesture is invalid")

    normalized: list[dict[str, int]] = []
    previous_t = -1
    for raw_point in points:
        if not isinstance(raw_point, dict):
            raise ValueError("interactive browser pointer gesture point is invalid")
        try:
            point_t = int(round(float(raw_point.get("t"))))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "interactive browser pointer gesture timing is invalid"
            ) from exc
        if point_t < previous_t or point_t < 0 or point_t > 5000:
            raise ValueError("interactive browser pointer gesture timing is invalid")
        normalized.append(
            {
                "x": _bounded_coordinate(
                    raw_point.get("x"),
                    default=0,
                    maximum=viewport["width"],
                ),
                "y": _bounded_coordinate(
                    raw_point.get("y"),
                    default=0,
                    maximum=viewport["height"],
                ),
                "t": point_t,
            }
        )
        previous_t = point_t
    return normalized

def _remaining_challenge_seconds(challenge: dict) -> float:
    try:
        expires = datetime.fromisoformat(challenge["expires_at"])
        now = datetime.now(expires.tzinfo or timezone.utc)
        return max(0.1, (expires - now).total_seconds())
    except (KeyError, TypeError, ValueError):
        return 300.0
