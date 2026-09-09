from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import threading
import time

from bscli.analytics.contracts import Budget, Query, bigint, decimal_hours, metrics, reject
from bscli.core.capability_runtime import CapabilityRejected

WHERE = "user_id = %s AND id = ANY(%s::bigint[]) AND log_date >= %s AND log_date < %s AND type_code = %s"
ROWS_SQL = "SELECT id,user_id,log_date,hours,type_code,project_id,created_at,updated_at FROM public.work_log WHERE " + WHERE + " ORDER BY id LIMIT 500"
AGGREGATE_SQL = """SELECT count(*) AS log_count, COALESCE(sum(hours),0) AS registered_hours,
 count(DISTINCT user_id) AS logged_people, count(DISTINCT log_date) AS logged_person_days,
 count(*) FILTER (WHERE project_id IS NULL) AS unassigned_project_log_count,
 COALESCE(sum(hours) FILTER (WHERE project_id IS NULL),0) AS unassigned_project_hours
 FROM public.work_log WHERE """ + WHERE
CONTRACT_SQL = """SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) AS type,
 has_column_privilege(c.oid,a.attname,'SELECT') AS readable
 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname='work_log' AND a.attnum>0 AND NOT a.attisdropped"""
ROLE_SQL = """SELECT current_setting('transaction_read_only')='on' AS readonly,
 current_setting('transaction_isolation')='repeatable read' AS repeatable,
 current_setting('default_transaction_read_only')='on' AS default_readonly,
 (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole) AS privileged,
 has_database_privilege(current_database(),'TEMP') AS temp,
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE CASE WHEN n.nspname='public' AND c.relkind='S'
 THEN has_sequence_privilege(c.oid,'USAGE') ELSE false END) AS sequence_usage,
 EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE CASE WHEN n.nspname='public' AND c.relkind IN ('r','p') THEN
 (has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') OR
 has_any_column_privilege(c.oid,'INSERT,UPDATE')) ELSE false END) AS writable,
 EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid='public.work_log'::regclass
 AND contype='p' AND convalidated AND conkey=ARRAY[(SELECT attnum FROM pg_catalog.pg_attribute
 WHERE attrelid='public.work_log'::regclass AND attname='id')]) AS primary_key
 FROM pg_catalog.pg_roles WHERE rolname=current_user"""
REQUIRED_COLUMNS = {"id": "bigint", "user_id": "bigint", "log_date": "date", "hours": "numeric(4,1)",
                    "type_code": "character varying(64)", "project_id": "bigint",
                    "created_at": "timestamp(6) without time zone", "updated_at": "timestamp(6) without time zone"}


def interruptible_query(gen, budget):
    """Check the deadline on each psycopg polling interval, including no socket activity."""
    try:
        state = next(gen)
        while True:
            ready = yield state
            budget.check()
            state = gen.send(ready)
    except StopIteration as result:
        return result.value
    finally:
        gen.close()


class PostgresReadExecutor:
    def __init__(self, secret_store, *, connect=None):
        self.secret_store, self.connect = secret_store, connect

    def summarize(self, config, snapshot: dict, query: Query, budget: Budget) -> dict:
        connection = None
        stop = threading.Event()
        watcher = None
        try:
            budget.check()
            config.validate()
            secret = self.secret_store.load(f"{config.source_id}:{config.credential_version}")
            password = secret.get("password")
            if not isinstance(password, str) or not password:
                reject("DATASOURCE_AUTH_FAILED")
            if self.connect is None:
                import psycopg
                from psycopg.rows import dict_row
                class BoundedConnection(psycopg.Connection):
                    def wait(self, gen, interval=0.1):
                        return super().wait(interruptible_query(gen, budget), interval=0.1)
                connect = lambda **kwargs: BoundedConnection.connect(row_factory=dict_row, **kwargs)
            else:
                connect = self.connect
            connection = connect(host=config.host, port=config.port, dbname=config.dbname,
                                 user=config.username, password=password, sslmode=config.sslmode,
                                 connect_timeout=5, autocommit=True,
                                 keepalives=1, keepalives_idle=5, keepalives_interval=1, keepalives_count=2,
                                 tcp_user_timeout=5000,
                                 application_name="agentbridge_personal_analytics",
                                 options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000 -c search_path=pg_catalog -c timezone=Asia/Shanghai")
            del password, secret
            def monitor():
                while not stop.wait(0.05):
                    if budget.canceled.is_set() or time.monotonic() >= budget.deadline:
                        try:
                            connection.cancel_safe(timeout=1)
                        except Exception:
                            pass
                        return
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            budget.check()
            role = connection.execute(ROLE_SQL).fetchone()
            if not role or not all(role.get(k) for k in ("readonly", "repeatable", "default_readonly", "primary_key")):
                reject("DATA_CONTRACT_CHANGED")
            if any(role.get(k) for k in ("privileged", "temp", "sequence_usage", "writable")):
                reject("DATASOURCE_POLICY_REJECTED")
            columns = connection.execute(CONTRACT_SQL).fetchall()
            actual = {r["attname"]: r["type"] for r in columns if r["readable"]}
            if any(actual.get(name) != kind for name, kind in REQUIRED_COLUMNS.items()):
                reject("DATA_CONTRACT_CHANGED")
            rows = [r for r in snapshot["rows"] if r["type_code"] == "DAILY"]
            parameters = (int(bigint(snapshot["principal_id"])), [int(bigint(r["id"])) for r in rows],
                          query.start, query.end, "DAILY")
            budget.check()
            db_rows = connection.execute(ROWS_SQL, parameters).fetchall()
            if len(db_rows) != len(rows) or len(db_rows) >= 500:
                reject("SOURCE_CHANGED")
            projected = []
            for row in db_rows:
                if bigint(row["user_id"]) != snapshot["principal_id"]:
                    reject("SOURCE_CHANGED")
                projected.append({"id": bigint(row["id"]), "log_date": row["log_date"].isoformat(),
                                  "type_code": row["type_code"], "hours": str(decimal_hours(row["hours"]).normalize()),
                                  "project_id": bigint(row["project_id"]) if row["project_id"] is not None else None,
                                  "created_at": row["created_at"].replace(microsecond=0).isoformat() if row["created_at"] else None})
            if projected != rows:
                reject("SOURCE_CHANGED")
            budget.check()
            aggregate = connection.execute(AGGREGATE_SQL, parameters).fetchone()
            expected = metrics(rows)
            for key, value in aggregate.items():
                if Decimal(str(value)) != Decimal(str(expected[key])):
                    reject("SOURCE_CHANGED")
            budget.check()
            return {"rows": projected, "evidence_rows": db_rows, "metrics": expected,
                    "database_read_at": datetime.now(timezone.utc).isoformat()}
        except CapabilityRejected:
            raise
        except Exception as exc:
            if budget.canceled.is_set():
                reject("INTERRUPTED")
            code = getattr(exc, "sqlstate", "") or ""
            if code.startswith("28"):
                reject("DATASOURCE_AUTH_FAILED")
            if code == "57014" or time.monotonic() >= budget.deadline:
                reject("BUDGET_EXCEEDED")
            if code.startswith("42"):
                reject("DATA_CONTRACT_CHANGED")
            reject("SOURCE_UNAVAILABLE")
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=2)
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                finally:
                    try:
                        connection.close()
                    except Exception:
                        pass
