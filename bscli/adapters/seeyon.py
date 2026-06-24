from __future__ import annotations

from bscli.core.config import SystemProfile
from bscli.core.registry import CommandDefinition, CommandRegistry


SEEYON_OA_URL = "http://10.10.50.110/seeyon/main.do?method=main"


def build_seeyon_profile() -> SystemProfile:
    return SystemProfile(
        id="oa",
        name="Seeyon OA",
        base_url=SEEYON_OA_URL,
        allowed_origins=["http://10.10.50.110"],
    )


def register_seeyon_commands(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            system="oa",
            name="api_inspect",
            description="Replay a discovered Seeyon OA backend API and summarize its response shape.",
            access="read",
            strategy="page_fetch",
            args_schema={
                "method": {"type": "string", "required": True},
                "url": {"type": "string", "required": True},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "inspection": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.inspection"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="api_replay",
            description="Replay a discovered Seeyon OA backend API inside the logged-in page context.",
            access="read",
            strategy="page_fetch",
            args_schema={
                "method": {"type": "string", "required": True},
                "url": {"type": "string", "required": True},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "integer"},
                    "ok": {"type": "boolean"},
                    "json": {"type": "object"},
                    "text": {"type": "string"},
                },
            },
            verify={"type": "json_path", "path": "$.status"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="api_save",
            description="Replay, inspect, and save a discovered Seeyon OA backend API as local BSCLI metadata.",
            access="read",
            strategy="page_fetch",
            args_schema={
                "name": {"type": "string", "required": True},
                "method": {"type": "string", "required": True},
                "url": {"type": "string", "required": True},
                "description": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "saved_path": {"type": "string"},
                    "inspection": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.saved_path"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="current_page_snapshot",
            description="Read the current Seeyon OA page title, URL, and visible text.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            verify={"type": "json_path", "path": "$.title"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="detail_read",
            description="Read a Seeyon OA detail page by URL and extract text, fields, attachments, and workflow hints.",
            access="read",
            strategy="page_fetch",
            args_schema={
                "url": {"type": "string", "required": True},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "fields": {"type": "array"},
                    "attachments": {"type": "array"},
                    "workflow": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.text"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="navigation_inventory",
            description="Read Seeyon OA portal tabs, left navigation shortcuts, and home-page sections.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "portal_count": {"type": "integer"},
                    "portals": {"type": "array"},
                    "shortcut_count": {"type": "integer"},
                    "shortcuts": {"type": "array"},
                    "section_count": {"type": "integer"},
                    "sections": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.shortcuts"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="page_inventory",
            description="Inventory the current Seeyon OA page structure for adapter discovery.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "buttons": {"type": "array"},
                    "links": {"type": "array"},
                    "forms": {"type": "array"},
                    "resources": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.title"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="pending_list",
            description="Read structured pending items from the current Seeyon OA home page.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="pending_list_api",
            description="Read structured pending items by replaying the discovered Seeyon pendingSection API.",
            access="read",
            strategy="page_fetch",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "total": {"type": "integer"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="sent_list_api",
            description="Read structured sent items by replaying the discovered Seeyon sentSection API.",
            access="read",
            strategy="page_fetch",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "total": {"type": "integer"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="template_list_api",
            description="Read structured form templates by replaying the discovered Seeyon templeteSection API.",
            access="read",
            strategy="page_fetch",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "total": {"type": "integer"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="pending_detail",
            description="Read one pending item metadata from the current Seeyon OA home page by affair_id.",
            access="read",
            strategy="dom_read",
            args_schema={
                "affair_id": {"type": "string", "required": True},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "item": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.found"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="template_detail",
            description="Read one Seeyon OA form template metadata from the current home page by template_id.",
            access="read",
            strategy="dom_read",
            args_schema={
                "template_id": {"type": "string", "required": True},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "item": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.found"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="template_list",
            description="Read form templates from the current Seeyon OA home page without opening them.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="session_status",
            description="Check whether the BSCLI daemon has an active browser bridge client for Seeyon OA.",
            access="read",
            strategy="daemon_api",
            args_schema={},
            api={"path": "/commands/run", "method": "POST"},
            output_schema={
                "type": "object",
                "properties": {
                    "connected": {"type": "boolean"},
                    "client_count": {"type": "integer"},
                    "clients": {"type": "array"},
                    "suggestions": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.connected"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="network_probe_install",
            description="Install a page-world fetch/XHR probe in the current Seeyon OA tab.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "installed": {"type": "boolean"},
                },
            },
            verify={"type": "json_path", "path": "$.installed"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="network_log_snapshot",
            description="Read network records captured by the Seeyon OA page probe.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "records": {"type": "array"},
                    "resources": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.records"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="network_api_candidates",
            description="Analyze captured Seeyon OA network records and return backend API candidates.",
            access="read",
            strategy="dom_read",
            args_schema={},
            output_schema={
                "type": "object",
                "properties": {
                    "candidates": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.candidates"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_draft",
            description="Build a non-executing Seeyon OA write-operation draft plan.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "source_url": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "mode": {"type": "string"},
                    "safety": {"type": "object"},
                    "request": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.safety.will_execute"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_dry_run",
            description="Build and audit a non-executing Seeyon OA write-operation dry-run plan.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "source_url": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "mode": {"type": "string"},
                    "safety": {"type": "object"},
                    "request": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.request.status"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_execute",
            description="Reserved Seeyon OA write execution command. Currently returns a blocked plan only.",
            access="write",
            strategy="human_gate",
            risk="high",
            requires_confirmation=True,
            args_schema={
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "source_url": {"type": "string"},
                "confirm": {
                    "type": "boolean",
                    "required": True,
                    "description": "Must be true to confirm intent; production execution is still blocked.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "plan": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.plan.safety.will_execute"},
        )
    )
