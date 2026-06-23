from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_STRATEGIES = {
    "public_api",
    "daemon_api",
    "page_fetch",
    "dom_read",
    "ui_workflow",
    "human_gate",
}


@dataclass(frozen=True)
class CommandDefinition:
    system: str
    name: str
    access: str
    strategy: str
    args_schema: dict[str, Any]
    description: str = ""
    risk: str = "low"
    output_schema: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[tuple[str, str], CommandDefinition] = {}

    def register(self, command: CommandDefinition) -> None:
        self._validate(command)
        key = (command.system, command.name)
        if key in self._commands:
            raise ValueError(f"command already registered: {command.system}.{command.name}")
        self._commands[key] = command

    def get(self, system: str, name: str) -> CommandDefinition:
        key = (system, name)
        if key not in self._commands:
            raise KeyError(f"command not found: {system}.{name}")
        return self._commands[key]

    def list(self, system: str | None = None) -> list[CommandDefinition]:
        commands = list(self._commands.values())
        if system is not None:
            commands = [command for command in commands if command.system == system]
        return sorted(commands, key=lambda command: (command.system, command.name))

    def _validate(self, command: CommandDefinition) -> None:
        if command.access not in {"read", "write"}:
            raise ValueError("access must be read or write")
        if command.strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(f"unsupported strategy: {command.strategy}")
        if command.access == "write" and not command.requires_confirmation:
            raise ValueError("write command must require confirmation")
        if command.strategy == "daemon_api" and not command.api:
            raise ValueError("daemon_api command must declare api")

