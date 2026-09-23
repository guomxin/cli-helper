"""User-level business permissions. Database capabilities remain in DatabaseGrants."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3


_CATALOG = json.loads(Path(__file__).with_name("user_permission_catalog.json").read_text(encoding="utf-8"))
PERMISSIONS = {item["id"]: item for item in _CATALOG["permissions"] if not item["id"].startswith("database.")}
CAPABILITY_PERMISSIONS = {
    capability: item["id"]
    for item in PERMISSIONS.values()
    for capability in item["capabilities"]
}
TOOL_PERMISSIONS = {
    tool: item["id"]
    for item in PERMISSIONS.values()
    for tool in (*item["tools"], *item.get("private_tools", []))
}
SPECIAL_TOOLS = _CATALOG["special_public_tools"]
REPORT_DEPENDENCIES = _CATALOG["report_dependencies"]


class UserGrantConflict(RuntimeError):
    pass


class UserGrants:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS user_business_grants (
                user_subject TEXT PRIMARY KEY, permissions_json TEXT NOT NULL,
                revision INTEGER NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS user_business_grant_events (
                event_id TEXT PRIMARY KEY, user_subject TEXT NOT NULL, actor TEXT NOT NULL,
                reason TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL
            )""")

    def get(self, subject: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT permissions_json, revision FROM user_business_grants WHERE user_subject = ?",
                (subject,),
            ).fetchone()
        if row is None:
            return None
        return {"user_subject": subject, "permissions": json.loads(row[0]), "revision": row[1]}

    def save(
        self, subject: str, permissions: list[str], *, expected_revision: int,
        actor: str, reason: str,
    ) -> dict:
        import datetime
        import uuid

        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("user subject is required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid grant revision")
        if not isinstance(permissions, list) or any(
            not isinstance(item, str) or item not in PERMISSIONS for item in permissions
        ):
            raise ValueError("unknown business permission")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("grant change reason is required")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("grant change actor is required")
        normalized = sorted(set(permissions))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT permissions_json, revision FROM user_business_grants WHERE user_subject = ?",
                (subject,),
            ).fetchone()
            old = json.loads(row[0]) if row else []
            revision = row[1] if row else 0
            if revision != expected_revision:
                raise UserGrantConflict("user grant revision conflict")
            db.execute(
                """INSERT INTO user_business_grants VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_subject) DO UPDATE SET
                   permissions_json = excluded.permissions_json,
                   revision = excluded.revision, updated_at = excluded.updated_at""",
                (subject, json.dumps(normalized), revision + 1, now),
            )
            db.execute(
                "INSERT INTO user_business_grant_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), subject, actor.strip(), reason.strip(),
                 json.dumps(old), json.dumps(normalized), revision + 1, now),
            )
        return {"user_subject": subject, "permissions": normalized, "revision": revision + 1}

    def require(self, subject: str, permission: str) -> None:
        grant = self.get(subject)
        if grant is None or permission not in grant["permissions"]:
            raise PermissionError(f"user lacks business permission: {permission}")

    def require_capability(self, subject: str, capability: str, arguments: dict | None = None) -> None:
        grant = self.get(subject)
        if grant is None:
            return
        if capability in {
            "oa.standard_collaboration.approval.prepare",
            "oa.standard_collaboration.approve",
        }:
            raise PermissionError("standard collaboration requires trusted form classification")
        permission = CAPABILITY_PERMISSIONS.get(capability)
        if permission:
            self.require(subject, permission)
        elif capability == "oa.template.list":
            raise PermissionError("template list requires permission-aware filtering")
        elif capability == "oa.workflow.pending.batch.prepare":
            self.require(subject, "oa.workflow.read")
        elif capability in {"oa.addressbook.group.list", "oa.addressbook.group.members"}:
            self.require_tool(subject, capability.replace(".", "_"), arguments)
        else:
            raise PermissionError("capability has no business permission mapping")
        if capability in {"oa.addressbook.export", "smartlight.report.export"}:
            self.require_tool(subject, capability.replace(".", "_"), arguments)

    def require_tool(self, subject: str, tool: str, arguments: dict | None = None) -> None:
        grant = self.get(subject)
        if grant is None:
            return  # Per-user migration: legacy checks remain authoritative until cutover.
        if tool.startswith("agentbridge_"):
            return  # Platform tools enforce their own host, task and object bindings.
        selected = set(grant["permissions"])
        args = arguments or {}
        permission = TOOL_PERMISSIONS.get(tool)
        if permission and permission not in selected:
            raise PermissionError(f"user lacks business permission: {permission}")
        if tool in {"oa_certificate_prepare_download", "oa_certificate_prepare_downloads"}:
            if "oa.certificate.read" not in selected:
                raise PermissionError("certificate download requires certificate read permission")
        if tool in {"oa_addressbook_group_list", "oa_addressbook_group_members"}:
            group_type = args.get("group_type")
            if group_type is None and tool.endswith("group_list"):
                if not {"oa.addressbook.read", "oa.addressbook.private.read"}.issubset(selected):
                    raise PermissionError("user lacks addressbook permissions")
            else:
                required = "oa.addressbook.private.read" if group_type == "private" else "oa.addressbook.read"
                if group_type not in {"private", "personal", "system", "project"} or required not in selected:
                    raise PermissionError("user lacks addressbook group permission")
        if tool == "oa_addressbook_export":
            source = args.get("source")
            required = "oa.addressbook.private.read" if source == "private_contacts" or (
                source == "group_members" and args.get("group_type") == "private"
            ) else "oa.addressbook.read"
            if required not in selected:
                raise PermissionError("user lacks addressbook source permission")
        if tool == "smartlight_report_export":
            required = REPORT_DEPENDENCIES.get(args.get("report_type"))
            if required is None or required not in selected:
                raise PermissionError("user lacks report source permission")
        if tool in SPECIAL_TOOLS:
            if tool == "oa_template_list" and not any(
                item.startswith("oa.") and (item.endswith(".draft") or item.endswith(".submit"))
                for item in selected
            ):
                raise PermissionError("user lacks form creation permission")
            if tool == "oa_workflow_pending_batch_prepare":
                if "oa.workflow.read" not in selected:
                    raise PermissionError("batch approval requires workflow read permission")
            if tool.endswith(("_session_status", "_session_login")):
                system = tool.split("_", 1)[0]
                if not any(item.startswith(system + ".") for item in selected):
                    raise PermissionError("user lacks system business permission")
            return
        if tool not in TOOL_PERMISSIONS:
            raise PermissionError("business tool has no permission mapping")

    def available_tools(self, subject: str, legacy_tools: list[str]) -> list[str]:
        grant = self.get(subject)
        if grant is None:
            return legacy_tools
        selected = set(grant["permissions"])
        available = []
        for tool in (*TOOL_PERMISSIONS, *SPECIAL_TOOLS):
            if tool.startswith("agentbridge_") or tool.startswith("database_"):
                available.append(tool)
            elif tool in TOOL_PERMISSIONS and TOOL_PERMISSIONS[tool] in selected and not (
                tool in {"oa_certificate_prepare_download", "oa_certificate_prepare_downloads"}
                and "oa.certificate.read" not in selected
            ):
                available.append(tool)
            elif tool.endswith(("_session_status", "_session_login")) and any(
                item.startswith(tool.split("_", 1)[0] + ".") for item in selected
            ):
                available.append(tool)
            elif tool == "oa_addressbook_group_list" and {
                "oa.addressbook.read", "oa.addressbook.private.read"
            }.issubset(selected):
                available.append(tool)
            elif tool == "oa_addressbook_group_members" and selected.intersection({
                "oa.addressbook.read", "oa.addressbook.private.read"
            }):
                available.append(tool)
            elif tool == "oa_workflow_pending_batch_prepare" and "oa.workflow.read" in selected:
                available.append(tool)
        return sorted(set(available))

    def effective_legacy_scopes(self, subject: str) -> list[str] | None:
        grant = self.get(subject)
        if grant is None:
            return None
        scopes = {"agentbridge:connect"} | {
            scope
            for permission in grant["permissions"]
            for scope in PERMISSIONS[permission]["legacy_scopes"]
        }
        for system in ("oa", "taihua", "smartlight", "yuque"):
            if any(permission.startswith(system + ".") for permission in grant["permissions"]):
                scopes.add(system + ":read")
        return sorted(scopes)
