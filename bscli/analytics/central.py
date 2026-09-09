"""Central-only analytics routing. Persist references; hydrate after authorization."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
import re
from uuid import uuid4
from datetime import datetime, timezone
from bscli.analytics.reports import EXPORT, DOWNLOAD, EXPORT_SCOPES, issue

from bscli.analytics.contracts import CAPABILITIES, RESULT_GET, SCOPES, SUMMARY, Budget, Query, reject
from bscli.analytics.results import AnalysisResultStore
from bscli.analytics.taihua_personal import TaihuaAnalyticsService
from bscli.analytics.visibility import PersonalVisibilityProvider
from bscli.core.capability_runtime import CapabilityEngine, CapabilityRejected, RequiresUserAction
from bscli.core.data_source_secrets import DataSourceSecretStore, ProtectedJsonStore
from bscli.core.data_sources import DataSourceConfig
from bscli.database.postgres_read import PostgresReadExecutor


class CentralAnalytics:
    def __init__(self, service):
        self.service = service
        self.root = service.home / "analytics"
        protector = service.session_states.protector
        self.results = AnalysisResultStore(service.db_path, self.root / "results", protector=protector)
        self.authorities = ProtectedJsonStore(self.root / "authorities", purpose="agentbridge.analytics-authority.v1", protector=protector)
        self.secrets = DataSourceSecretStore(self.root / "credentials", protector=protector)
        self.executor = PostgresReadExecutor(self.secrets)
        self.guard = threading.Lock()
        self.active = {}
        self.instance_lock = None

    def config(self):
        return DataSourceConfig.load(self.root / "source.json")

    def _claim_instance(self):
        """An enabled source cannot be shared by two processes using this home."""
        if self.instance_lock is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        stream = (self.root / "single-instance.lock").open("a+b")
        try:
            import os
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            reject("SOURCE_UNAVAILABLE")
        self.instance_lock = stream
        # Only the exclusive source owner can recover abandoned reads.
        with self.service.operations._connect() as conn:
            pending = conn.execute("SELECT operation_id FROM operations WHERE capability_name IN (?,?,?,?) AND status IN ('pending','running')", (SUMMARY, RESULT_GET, EXPORT, DOWNLOAD)).fetchall()
        for row in pending:
            self.service.operations.mark_failed(row["operation_id"], code="INTERRUPTED", message="分析服务已重启，请重新查询。")

    @contextmanager
    def lease(self, owner, request_id, task_id, event):
        with self.guard:
            if owner in self.active or len(self.active) >= 2:
                reject("ANALYSIS_BUSY")
            self._claim_instance()
            done = threading.Event()
            self.active[owner] = (request_id, task_id, event, done)
        try:
            yield
        finally:
            with self.guard:
                self.active.pop(owner, None)
                done.set()

    def cancel(self, owner, *, request_id=None, task_id=None):
        with self.guard:
            entry = self.active.get(owner)
            if entry is None or not ((request_id and entry[0] == request_id) or (task_id and entry[1] == task_id)):
                return None
            entry[2].set()
            done = entry[3]
        return done.wait(0.1)

    def invoke(self, *, user_subject, capability_name, arguments, idempotency_key=None,
               request_id=None, task_id=None, authority_id=None, cancellation=None, **_kwargs):
        service = self.service
        request_id = request_id or str(uuid4())
        event = cancellation or threading.Event()
        trace, _ = service.runtime_governance.ensure_trace(
            user_subject=user_subject, request_id=request_id, task_id=task_id,
            host_type=_kwargs.get("host_type", "unknown"), request_kind="read",
            system_id="taihua", capability_name=capability_name)
        captured = {}
        def authority():
            if not authority_id or service._task_plan_authority_resolver is None:
                return False
            try:
                if task_id:
                    from bscli.core.tasks import ACTIVE_TASK_STATUSES
                    task = service.tasks.get_task(task_id, user_subject=user_subject)
                    if task["status"] not in ACTIVE_TASK_STATUSES and not (
                            task["status"] == "succeeded" and capability_name in {RESULT_GET, EXPORT, DOWNLOAD}):
                        return False
                resolved = service._task_plan_authority_resolver(authority_id, EXPORT_SCOPES if capability_name in {EXPORT, DOWNLOAD} else SCOPES)
                return resolved["user_subject"] == user_subject
            except Exception:
                return False
        def phase(name):
            service.runtime_governance.record_stage_once(trace_id=trace["trace_id"],
                stage="analytics." + name, status="succeeded", system_id="taihua", capability_name=capability_name)
            if task_id:
                from bscli.core.tasks import ACTIVE_TASK_STATUSES
                if service.tasks.get_task(task_id, user_subject=user_subject)["status"] in ACTIVE_TASK_STATUSES:
                    service.tasks.record_analysis_phase(task_id=task_id, user_subject=user_subject, phase=name, request_id=request_id)
        budget = Budget(canceled=event, authority=authority, phase=phase)

        def execute(context, inputs):
            if task_id:
                service.observe_host_task(user_subject=user_subject, task_id=task_id,
                                         operation_ids=[context.operation_id], interaction_ids=[])
            self.authorities.save(context.operation_id, {"authority_id": authority_id, "owner": user_subject})
            if capability_name == SUMMARY:
                query = Query.parse(inputs)
            else:
                query = None
            try:
                return self._with_session(user_subject, budget, lambda session, provider, config:
                    self._execute_in_session(context, inputs, query, session, provider, config, budget, captured))
            except (CapabilityRejected, RequiresUserAction):
                raise
            except Exception:
                reject("SOURCE_UNAVAILABLE")

        engine = CapabilityEngine(registry=service.registry, operation_store=service.operations)
        engine.register_handler(capability_name, execute)
        try:
            budget.check()
            self.config().authorize(user_subject)
            if capability_name == SUMMARY:
                Query.parse(arguments)
            elif (not isinstance(arguments, dict) or set(arguments) != {"report_id" if capability_name == DOWNLOAD else "result_id"}
                  or not isinstance(next(iter(arguments.values())), str)
                  or not re.fullmatch(r"[0-9a-f]{32}", next(iter(arguments.values())))):
                reject("INVALID_ANALYSIS_INPUT")
            if _kwargs.get("host_type") == "task_plan" and capability_name != SUMMARY:
                reject("DATA_ACCESS_DENIED")
            with self.lease(user_subject, request_id, task_id, event):
                response = engine.invoke(user_subject=user_subject, capability_name=capability_name,
                    arguments=arguments, idempotency_key=idempotency_key, request_id=request_id,
                    trace_id=trace["trace_id"], task_id=task_id)
                if response["status"] == "succeeded":
                    reference = response["result"]
                    if capability_name in {EXPORT, DOWNLOAD} and (response["reused"] or not captured):
                        value = self._with_session(user_subject, budget, lambda session, provider, config:
                            self._report_value(reference["result_id"], user_subject, session, provider, budget,
                                               download=capability_name == DOWNLOAD))
                    elif response["reused"] or not captured:
                        value = self._with_session(user_subject, budget, lambda session, provider, config:
                            TaihuaAnalyticsService(self.executor, self.results).get(
                                result_id=reference["result_id"], owner=user_subject, session=session, provider=provider, budget=budget))
                    else:
                        value = captured["value"]
                    budget.check()
                    if capability_name in {EXPORT, DOWNLOAD} and task_id:
                        report_id = reference["result_id"]
                        report = self.results.load(report_id, owner=user_subject)
                        import base64
                        artifact, _ = service.tasks.link_artifact(task_id=task_id, user_subject=user_subject,
                            artifact={"artifact_type": "taihua_personal_csv", "source_ref": report_id + ":" + task_id,
                                "filename": report["filename"], "content_type": "text/csv",
                                "byte_size": len(base64.b64decode(report["content_base64"])),
                                "download_url": f"/api/analytics/reports/{report_id}/download",
                                "expires_at": report["download_expires_at"]})
                        value = {**value, "artifact_id": artifact["artifact_id"]}
                    budget.check()
                    response = {**response, "result": reference if _kwargs.get("host_type") == "task_plan" else value}
        except RequiresUserAction as exc:
            response = {"protocolVersion": "0.1", "requestId": request_id, "status": "requires_user_action",
                        "result": None, "error": {"code": exc.code, "message": exc.message}, "nextAction": exc.next_action}
        except CapabilityRejected as exc:
            response = {"protocolVersion": "0.1", "requestId": request_id, "status": "failed",
                        "result": None, "error": {"code": exc.code, "message": exc.message}}
        except Exception:
            response = {"protocolVersion": "0.1", "requestId": request_id, "status": "failed",
                        "result": None, "error": {"code": "SOURCE_UNAVAILABLE", "message": "分析服务暂不可用。"}}
        status = response["status"]
        if status != "succeeded" and captured.get("new_result_id"):
            self.results.discard(captured["new_result_id"], owner=user_subject)
        trace_status = "waiting" if status == "requires_user_action" else "active" if status in {"pending", "running"} else status
        service.runtime_governance.update_trace(trace["trace_id"], status=trace_status, finished=trace_status not in {"waiting", "active"})
        if task_id and event.is_set():
            from bscli.core.tasks import ACTIVE_TASK_STATUSES
            if service.tasks.get_task(task_id, user_subject=user_subject)["status"] in ACTIVE_TASK_STATUSES:
                service.tasks.cancel_task(task_id=task_id, user_subject=user_subject,
                                         reason="analysis_execution_stopped", causation_ref=request_id)
        if task_id and response.get("operationId") and not event.is_set() and _kwargs.get("host_type") not in {"interaction_resume", "task_plan"}:
            service.observe_host_task(user_subject=user_subject, task_id=task_id,
                                     operation_ids=[response["operationId"]], interaction_ids=[])
        return {**response, "runtimeTraceId": trace["trace_id"]}

    def _execute_in_session(self, context, inputs, query, session, provider, config, budget, captured):
        analytics = TaihuaAnalyticsService(self.executor, self.results)
        if context.spec.name == SUMMARY:
            reference, value = analytics.run(config=config, owner=context.user_subject,
                operation_id=context.operation_id, session=session, provider=provider, query=query, budget=budget)
            captured["new_result_id"] = reference["result_id"]
        elif context.spec.name == EXPORT:
            summary = analytics.get(result_id=inputs["result_id"], owner=context.user_subject,
                                    session=session, provider=provider, budget=budget)
            reference, value = issue(self.results, owner=context.user_subject, operation_id=context.operation_id,
                                     result_id=inputs["result_id"], summary=summary,
                                     authority_id=self.authorities.load(context.operation_id)["authority_id"])
            captured["new_result_id"] = reference["result_id"]
        elif context.spec.name == DOWNLOAD:
            value = self._report_value(inputs["report_id"], context.user_subject, session, provider, budget, download=True)
            reference = {"result_id": inputs["report_id"], "protected": True, "schema_version": "taihua.analytics.reference.v1"}
        else:
            value = analytics.get(result_id=inputs["result_id"], owner=context.user_subject,
                                  session=session, provider=provider, budget=budget)
            reference = {"result_id": inputs["result_id"], "protected": True,
                         "schema_version": "taihua.analytics.reference.v1"}
        captured["value"] = value
        return reference

    def _report_value(self, report_id, owner, session, provider, budget, *, download):
        payload = self.results.load(report_id, owner=owner)
        if payload.get("kind") != "csv":
            reject("INVALID_ANALYSIS_INPUT")
        TaihuaAnalyticsService(self.executor, self.results).get(result_id=payload["source_result_id"],
            owner=owner, session=session, provider=provider, budget=budget)
        self.results.load(report_id, owner=owner)
        if payload["download_expires_at"] <= datetime.now(timezone.utc).isoformat():
            reject("RESULT_EXPIRED")
        budget.check()
        if download:
            return {"schemaVersion": "agentbridge.protected_csv_delivery.v1", "report_id": report_id,
                    "file": {k: payload[k] for k in ("filename", "content_type", "content_base64")}}
        return {"report_id": report_id, "artifact_type": "taihua_personal_csv", "filename": payload["filename"],
                "download_expires_at": payload["download_expires_at"],
                "expires_at": self.results.expires_at(report_id, owner=owner).isoformat(),
                "download_tool": "taihua_analytics_report_download", "authentication_required": True}

    def compare_plan(self, plan, arguments, operation_id):
        from bscli.analytics.comparison import compare
        owner = plan["user_subject"]
        def authority():
            try:
                current = self.service.task_plans.get(plan["plan_id"], user_subject=owner)
                self.service.validate_task_plan_execution(current)
                return True
            except Exception:
                return False
        budget = Budget(authority=authority)
        def action(session, provider, config):
            analytics = TaihuaAnalyticsService(self.executor, self.results)
            ids = [arguments[key]["result_id"] for key in ("baseline", "current")]
            values = [analytics.get(result_id=rid, owner=owner, session=session, provider=provider, budget=budget) for rid in ids]
            output = compare(*values, allow_unequal_windows=arguments["allow_unequal_windows"])
            budget.check()
            return self.results.save(owner=owner, operation_id=operation_id,
                payload={"kind": "comparison", "source_result_ids": ids, "output": output},
                expires_at=min(self.results.expires_at(rid, owner=owner) for rid in ids))
        captured = {}
        def execute(session, provider, config):
            reference = action(session, provider, config)
            captured["result_id"] = reference["result_id"]
            return reference
        try:
            with self.lease(owner, operation_id, plan["parent_task_id"], budget.canceled):
                return self._with_session(owner, budget, execute)
        except Exception:
            if captured:
                self.results.discard(captured["result_id"], owner=owner)
            raise

    def _with_session(self, owner, budget, action):
        from bscli.adapters.taihua import TaihuaLoginRequired
        from bscli.core.central_service import login_required_action
        service = self.service
        config = self.config()
        config.authorize(owner)
        budget.phase("identity")
        runtime = service._runtime_for_system("taihua")
        if runtime is None:
            reject("SOURCE_UNAVAILABLE")
        adapter, factory = runtime
        session = service.sessions.find(user_subject=owner, system_id="taihua")
        if session is None or session["state"] != "active":
            raise login_required_action(owner, "taihua", session)
        with service._session_lock(session["session_id"], wait_seconds=1):
            session = service.sessions.get(session["session_id"])
            if session["state"] != "active":
                raise login_required_action(owner, "taihua", session)
            state = service.session_states.load(session["session_id"])
            if state is None:
                raise login_required_action(owner, "taihua", session)
            with factory(session, adapter) as worker:
                worker.restore_session_state(state)
                provider = PersonalVisibilityProvider(adapter, worker, expected_principal=session["expected_principal_ref"],
                                                       expected_id=session.get("analytics_principal_id"))
                original_capture = provider.capture
                def capture(query, selected_budget):
                    snapshot = original_capture(query, selected_budget)
                    service.sessions.bind_analytics_principal(session["session_id"], snapshot["principal_id"])
                    provider.expected_id = snapshot["principal_id"]
                    return snapshot
                provider.capture = capture
                try:
                    result = action(session, provider, config)
                    current = service.sessions.get(session["session_id"])
                    if current["state"] != "active" or current["authority_generation"] != session["authority_generation"]:
                        reject("SOURCE_CHANGED")
                    if self.config() != config:
                        reject("SOURCE_CHANGED")
                    budget.check()
                    return result
                except TaihuaLoginRequired:
                    service.sessions.mark_expired(session["session_id"], "分析时发现会话失效。")
                    raise login_required_action(owner, "taihua", service.sessions.get(session["session_id"]))
                except CapabilityRejected as exc:
                    if exc.code == "IDENTITY_CHANGED":
                        service.sessions.quarantine(session["session_id"], "Analytics principal identity changed.")
                    raise
                finally:
                    current = service.sessions.get(session["session_id"])
                    if current["state"] == "active" and current["authority_generation"] == session["authority_generation"]:
                        service.session_states.save(session["session_id"], worker.capture_session_state())
