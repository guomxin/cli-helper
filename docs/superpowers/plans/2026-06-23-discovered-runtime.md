# Discovered API Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn saved `.bscli/discovered/<system>/apis/*.json` API metadata into listable, runnable, and manifest-exportable dynamic commands.

**Architecture:** Add a small discovered API store that reads saved metadata from disk and normalizes it into command-like records. The CLI gets `discovered list/show/run`; daemon gets `discovered_run`; tool manifest export receives optional discovered tools without changing static adapter registration.

**Tech Stack:** Python standard library, existing `bscli` CLI/daemon/manifest patterns, unittest.

---

### Task 1: Discovered API Store

**Files:**
- Create: `bscli/core/discovered.py`
- Test: `tests/test_discovered_store.py`

- [x] Write failing tests for loading, showing, and slug-to-tool-name conversion.
- [x] Implement `DiscoveredApi`, `DiscoveredApiStore.list_apis`, `load_api`, and `tool_name`.
- [x] Reject unsafe names by allowing only saved JSON files under `.bscli/discovered/<system>/apis`.

### Task 2: CLI Commands

**Files:**
- Modify: `bscli/cli/main.py`
- Test: `tests/test_cli_discovered.py`

- [x] Add `discovered list <system>` to print saved API summaries.
- [x] Add `discovered show <system> <name>` to print one saved API record.
- [x] Add `discovered run <system> <name>` to call daemon `/commands/run` with `command=discovered_run`.

### Task 3: Daemon Runtime

**Files:**
- Modify: `bscli/daemon/app.py`
- Test: `tests/test_daemon.py`

- [x] Add `discovered_run` command handling.
- [x] Load the saved API by `name`, execute its stored request through `page_fetch`, inspect the response, and return `{api, request, inspection, replay}`.
- [x] Keep execution read-only in v1 and reject missing API names clearly.

### Task 4: Manifest Export

**Files:**
- Modify: `bscli/core/tool_manifest.py`
- Modify: `bscli/cli/main.py`
- Test: `tests/test_cli_discovered.py`

- [x] Export saved APIs as tools named like `oa__discovered__template_section`.
- [x] Use the saved inspection summary in tool descriptions/metadata.
- [x] Keep static adapter tools unchanged.

### Task 5: Docs And Verification

**Files:**
- Modify: `README.md`

- [x] Document the full flow: `api_save -> discovered list/show/run -> tool manifest`.
- [x] Run targeted tests.
- [x] Run `python -m unittest discover`.
- [x] Restart daemon and run a real saved `template-section` discovered command.
