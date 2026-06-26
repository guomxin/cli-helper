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
            name="history_sections",
            description="Read Seeyon OA historical workflow tabs such as sent, done, and tracked.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "kind": {
                    "type": "string",
                    "description": "Optional historical collection filter: sent, done, or tracked.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
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
            name="history_list",
            description="Read Seeyon OA historical workflows from the sentSection tab API without clicking the page.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "kind": {
                    "type": "string",
                    "description": "Historical collection: sent, done, or tracked. Defaults to done.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "kind": {"type": "string"},
                    "count": {"type": "integer"},
                    "items": {"type": "array"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="history_profile",
            description="Cluster Seeyon OA historical workflows by title pattern, category, status, and frequency.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "kind": {
                    "type": "string",
                    "description": "Historical collection to profile: sent, done, tracked, or all. Defaults to done.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "source_count": {"type": "integer"},
                    "cluster_count": {"type": "integer"},
                    "clusters": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.clusters"},
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
            name="template_match",
            description="Match high-frequency Seeyon OA historical workflow clusters to launchable form templates.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "kind": {
                    "type": "string",
                    "description": "Historical collection to profile before matching: sent, done, tracked, or all.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "clusters": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.clusters"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="matter_profile",
            description="Build a Seeyon OA matter catalog from historical workflow clusters and matching form templates.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "kind": {
                    "type": "string",
                    "description": "Historical collections to profile: sent, done, tracked, or all. Defaults to all.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "matter_count": {"type": "integer"},
                    "matched_template_count": {"type": "integer"},
                    "matters": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.matters"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="matter_inspect",
            description="Inspect one Seeyon OA matter type, its matched template, and optional launch-page fields.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "id": {"type": "string", "description": "Matter id / cluster id to inspect."},
                "name": {"type": "string", "description": "Matter name or title pattern to inspect."},
                "kind": {
                    "type": "string",
                    "description": "Historical collections to profile: sent, done, tracked, or all. Defaults to all.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
                "with_launch": {
                    "type": "boolean",
                    "description": "When true, open the matched template launch page read-only to inspect fields.",
                },
                "settle_ms": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "matter": {"type": "object"},
                    "launch_inspection": {"type": "object"},
                    "next_steps": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.matter"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="launch_inspect",
            description="Open a Seeyon OA template launch page and inspect fields, buttons, and write hints without submitting.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "template_id": {"type": "string", "description": "Template id to resolve from oa template list."},
                "url": {"type": "string", "description": "Direct launch/new-flow page URL to inspect."},
                "settle_ms": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "fields": {"type": "array"},
                    "buttons": {"type": "array"},
                    "actions": {"type": "array"},
                    "safety": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.safety"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="launch_dry_run",
            description="Precheck a Seeyon OA template launch-page save-draft operation without mutating OA state.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "template_id": {"type": "string", "description": "Template id to resolve from oa template list."},
                "url": {"type": "string", "description": "Direct launch/new-flow page URL to precheck."},
                "fields": {"type": "object", "description": "Field name/id/label to value mapping to validate."},
                "settle_ms": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "mode": {"type": "string"},
                    "fields": {"type": "array"},
                    "safety": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.safety.will_execute"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="launch_save_draft",
            description="Execute a confirmed Seeyon OA launch-page save-draft operation through the logged-in Chrome bridge without sending the workflow.",
            access="write",
            strategy="human_gate",
            risk="medium",
            requires_confirmation=True,
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "template_id": {"type": "string", "description": "Template id to resolve from oa template list."},
                "url": {"type": "string", "description": "Direct launch/new-flow page URL to save as draft."},
                "fields": {"type": "object", "required": True, "description": "Field name/id/label to value mapping to fill."},
                "confirm": {
                    "type": "boolean",
                    "required": True,
                    "description": "Must be true to confirm creating or updating a launch-page draft.",
                },
                "settle_ms": {"type": "integer"},
                "keep_tab": {"type": "boolean"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "draft_saved": {"type": "boolean"},
                    "submitted_count": {"type": "integer"},
                    "plan": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.draft_saved"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="workflow_list",
            description="Read Seeyon OA workflow items from the pending or sent collection.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
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
            name="workflow_detail",
            description="Read a Seeyon OA workflow detail page by affair_id or rendered detail URL.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "include": {"type": "string"},
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source_item": {"type": "object"},
                    "detail": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.detail"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="workflow_inspect",
            description="Read one Seeyon OA workflow as an agent-ready intelligence packet with detail summary and read-effect metadata.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "include": {"type": "string"},
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source_item": {"type": "object"},
                    "detail": {"type": "object"},
                    "summary": {"type": "object"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.summary"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="workflow_brief",
            description="Read a list-only Seeyon OA workflow brief without opening detail pages.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "count": {"type": "integer"},
                    "items": {"type": "array"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="workflow_evidence",
            description="Read one Seeyon OA workflow and return a compact evidence packet for agent decisions.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {"type": "string", "description": "Workflow collection: pending or sent. Defaults to pending."},
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source_item": {"type": "object"},
                    "evidence": {"type": "object"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.evidence"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="workflow_timeline",
            description="Read and normalize the workflow opinion timeline for one Seeyon OA workflow.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {"type": "string", "description": "Workflow collection: pending or sent. Defaults to pending."},
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source_item": {"type": "object"},
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
            name="workflow_opinions",
            description="Read workflow opinions for one or more Seeyon OA workflows.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {"type": "string", "description": "Workflow collection: pending or sent. Defaults to pending."},
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "keyword": {"type": "string", "description": "Keyword used when reading a batch of workflow opinions."},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "source_item": {"type": "object"},
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
            name="workflow_attachments",
            description="Read workflow detail attachments for one or more Seeyon OA workflows.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
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
            name="workflow_actions",
            description="Read candidate workflow actions for one or more Seeyon OA workflows without executing writes.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection: pending or sent. Defaults to pending.",
                },
                "id": {"type": "string", "description": "Workflow affair_id to resolve from the selected collection."},
                "url": {"type": "string", "description": "Rendered OA detail-page URL fallback."},
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
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
            name="write_capabilities",
            description="Read agent-facing write capabilities for pending Seeyon OA items without executing writes.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection to inspect; currently pending is the supported write target.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
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
            name="write_discover",
            description="Discover and aggregate candidate write actions from historical details or launch-page inspection without executing writes.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "source": {
                    "type": "string",
                    "description": "Discovery source: history or launch.",
                },
                "kind": {
                    "type": "string",
                    "description": "Historical collection to sample: sent, done, or tracked. Defaults to done.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
                "template_id": {"type": "string"},
                "url": {"type": "string"},
                "settle_ms": {"type": "integer"},
                "deep_limit": {
                    "type": "integer",
                    "description": "Maximum number of detail pages to open for action discovery.",
                },
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "source": {"type": "string"},
                    "kind": {"type": "string"},
                    "actions": {"type": "array"},
                    "items": {"type": "array"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.actions"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_endpoint_candidates",
            description="Classify untested Seeyon OA write endpoint candidates for one workflow action without calling them.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "source_url": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "object"},
                    "action": {"type": "object"},
                    "endpoint_candidates": {"type": "array"},
                    "probe_policy": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.endpoint_candidates"},
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
            name="doctor",
            description="Check BSCLI/OA daemon, browser bridge, discovered API, and static capability readiness.",
            access="read",
            strategy="daemon_api",
            args_schema={},
            api={"path": "/commands/run", "method": "POST"},
            output_schema={
                "type": "object",
                "properties": {
                    "daemon": {"type": "object"},
                    "session": {"type": "object"},
                    "capabilities": {"type": "object"},
                    "discovered": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.daemon.ok"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="inbox_analyze",
            description="Analyze the OA inbox as an agent-ready read-only work queue; list-only by default, with explicit bounded deep reads.",
            access="read",
            strategy="daemon_api",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Inbox workflow collection: pending or sent. Defaults to pending.",
                },
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
                "deep": {
                    "type": "boolean",
                    "description": "Open detail pages for a limited number of items when true.",
                },
                "deep_limit": {
                    "type": "integer",
                    "description": "Maximum number of detail pages to open in deep mode.",
                },
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "count": {"type": "integer"},
                    "deep_count": {"type": "integer"},
                    "items": {"type": "array"},
                    "read_effect": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.items"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="capability_map",
            description="Return the agent-facing OA read/write/discovered capability map without executing writes.",
            access="read",
            strategy="daemon_api",
            args_schema={},
            api={"path": "/commands/run", "method": "POST"},
            output_schema={
                "type": "object",
                "properties": {
                    "read": {"type": "array"},
                    "write": {"type": "object"},
                    "discovered": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.read"},
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
            name="write_preflight",
            description="Build a non-executing Seeyon OA write preflight packet with dry-run decision, confirmation contract, and sanitized plan.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection to resolve; currently pending is supported.",
                },
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "source_url": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "decision": {"type": "object"},
                    "execution_contract": {"type": "object"},
                    "plan": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.decision.status"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_prepare",
            description="Build an agent-ready OA write task packet by combining workflow evidence with a non-executing preflight decision.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "type": {
                    "type": "string",
                    "description": "Workflow collection to resolve; currently pending is supported.",
                },
                "affair_id": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "source_url": {"type": "string"},
                "text_limit": {"type": "integer"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "target": {"type": "object"},
                    "workflow": {"type": "object"},
                    "preflight": {"type": "object"},
                    "next_steps": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.next_steps.status"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="write_execute",
            description="Execute a confirmed Seeyon OA ContinueSubmit write through the logged-in Chrome bridge.",
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
                    "description": "Must be true to confirm intent before production execution.",
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
    registry.register(
        CommandDefinition(
            system="oa",
            name="pending_submit",
            description="Submit matching Seeyon OA pending items one by one with action verification and post-submit disappearance checks.",
            access="write",
            strategy="human_gate",
            risk="high",
            requires_confirmation=True,
            args_schema={
                "keyword": {"type": "string", "required": True},
                "action": {"type": "string", "required": True},
                "opinion": {"type": "string"},
                "limit": {"type": "integer"},
                "confirm": {
                    "type": "boolean",
                    "required": True,
                    "description": "Must be true to confirm intent before batch production execution.",
                },
                "verify_wait": {
                    "type": "number",
                    "description": "Seconds to wait after each submit before reading pending items again.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "target_count": {"type": "integer"},
                    "submitted_count": {"type": "integer"},
                    "stopped": {"type": "boolean"},
                    "items": {"type": "array"},
                },
            },
            verify={"type": "json_path", "path": "$.submitted_count"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="meeting_reply_dry_run",
            description="Precheck a Seeyon OA meeting reply without mutating OA state.",
            access="read",
            strategy="daemon_api",
            risk="low",
            api={"path": "/commands/run", "method": "POST"},
            args_schema={
                "id": {"type": "string", "required": True, "description": "Pending affair_id to resolve to a meeting."},
                "meeting_id": {"type": "string"},
                "source_url": {"type": "string"},
                "attitude": {"type": "string", "description": "join, not_join, or pending. Defaults to join."},
                "feedback": {"type": "string"},
            },
            output_schema={
                "type": "object",
                "properties": {
                    "precheck": {"type": "object"},
                    "target": {"type": "object"},
                    "current_reply": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.precheck"},
        )
    )
    registry.register(
        CommandDefinition(
            system="oa",
            name="meeting_reply_execute",
            description="Execute a confirmed Seeyon OA meeting reply and verify the reply state by reading meetingView.",
            access="write",
            strategy="human_gate",
            risk="high",
            requires_confirmation=True,
            args_schema={
                "id": {"type": "string", "required": True, "description": "Pending affair_id to resolve to a meeting."},
                "meeting_id": {"type": "string"},
                "source_url": {"type": "string"},
                "attitude": {"type": "string", "description": "join, not_join, or pending. Defaults to join."},
                "feedback": {"type": "string"},
                "confirm": {
                    "type": "boolean",
                    "required": True,
                    "description": "Must be true to confirm intent before production execution.",
                },
                "verify_wait": {
                    "type": "number",
                    "description": "Seconds to wait after submit before reading meetingView again.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "submitted": {"type": "boolean"},
                    "verification": {"type": "object"},
                    "plan": {"type": "object"},
                },
            },
            verify={"type": "json_path", "path": "$.verification.status"},
        )
    )
