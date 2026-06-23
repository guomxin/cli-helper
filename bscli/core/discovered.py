from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any


DISCOVERED_API_SCHEMA = "bscli.discovered_api.v1"
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


@dataclass(frozen=True)
class DiscoveredApi:
    system: str
    name: str
    description: str
    access: str
    risk: str
    request: dict[str, Any]
    inspection: dict[str, Any]
    path: Path
    raw: dict[str, Any]

    @property
    def command_name(self) -> str:
        return f"discovered:{self.name}"

    @property
    def tool_name(self) -> str:
        return f"{self.system}__discovered__{_tool_slug(self.name)}"


class DiscoveredApiStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def list_apis(self, system: str) -> list[DiscoveredApi]:
        api_dir = self._api_dir(system)
        if not api_dir.exists():
            return []
        apis = []
        for path in sorted(api_dir.glob("*.json")):
            apis.append(self._load_path(system, path))
        return sorted(apis, key=lambda api: api.name)

    def load_api(self, system: str, name: str) -> DiscoveredApi:
        self._validate_name(name)
        path = self._api_dir(system) / f"{name}.json"
        if not path.exists():
            raise KeyError(f"discovered API not found: {system}.{name}")
        return self._load_path(system, path)

    def _api_dir(self, system: str) -> Path:
        self._validate_name(system)
        return self.root / "discovered" / system / "apis"

    def _load_path(self, system: str, path: Path) -> DiscoveredApi:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != DISCOVERED_API_SCHEMA:
            raise ValueError(f"unsupported discovered API schema: {path}")
        name = str(data.get("name") or path.stem)
        self._validate_name(name)
        request = data.get("request") or {}
        if not isinstance(request, dict) or not request.get("url"):
            raise ValueError(f"discovered API request.url is required: {path}")
        inspection = data.get("inspection") or {}
        return DiscoveredApi(
            system=system,
            name=name,
            description=str(data.get("description") or ""),
            access=str(data.get("access") or "read"),
            risk=str(data.get("risk") or "low"),
            request=request,
            inspection=inspection if isinstance(inspection, dict) else {},
            path=path,
            raw=data,
        )

    def _validate_name(self, value: str) -> None:
        if not value or not _SAFE_NAME_RE.match(value):
            raise ValueError(f"unsafe discovered API name: {value}")


def _tool_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
