from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class TransformRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


TransformHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class TransformSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    maximum_input_items: int
    maximum_output_chars: int
    halts_on_empty: bool = False

    @property
    def version(self) -> str:
        return self.name.rsplit(".v", 1)[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "maximumInputItems": self.maximum_input_items,
            "maximumOutputChars": self.maximum_output_chars,
            "haltsOnEmpty": self.halts_on_empty,
            "deterministic": True,
            "networkAccess": False,
        }


class TransformRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, TransformSpec] = {}
        self._handlers: dict[str, TransformHandler] = {}

    def register(self, spec: TransformSpec, handler: TransformHandler) -> None:
        if spec.name in self._specs and self._specs[spec.name] != spec:
            raise ValueError(f"transform already registered: {spec.name}")
        if not callable(handler):
            raise TypeError("transform handler must be callable")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def get(self, name: str) -> TransformSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown transform: {name}") from exc

    def list(self) -> list[TransformSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self.get(name)
        if not isinstance(arguments, dict):
            raise TransformRejected(
                "TRANSFORM_INPUT_INVALID",
                "transform input must be an object",
            )
        items = arguments.get("items")
        if not isinstance(items, list):
            raise TransformRejected(
                "TRANSFORM_INPUT_INVALID",
                "transform input items must be an array",
            )
        if len(items) > spec.maximum_input_items:
            raise TransformRejected(
                "TRANSFORM_INPUT_TOO_LARGE",
                f"transform accepts at most {spec.maximum_input_items} items",
            )
        result = self._handlers[name](arguments)
        if not isinstance(result, dict):
            raise TransformRejected(
                "TRANSFORM_OUTPUT_INVALID",
                "transform output must be an object",
            )
        draft = result.get("draft")
        if isinstance(draft, str) and len(draft) > spec.maximum_output_chars:
            raise TransformRejected(
                "TRANSFORM_OUTPUT_TOO_LARGE",
                f"transform output exceeds {spec.maximum_output_chars} characters",
            )
        return result


WORK_ITEMS_TO_LOG_DRAFT = "work_items_to_log_draft.v1"

WORK_ITEMS_TO_LOG_DRAFT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "affair_id": {"type": "string"},
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "category": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

WORK_ITEMS_TO_LOG_DRAFT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string"},
        "empty": {"type": "boolean"},
        "source_count": {"type": "integer"},
        "included_count": {"type": "integer"},
        "excluded_count": {"type": "integer"},
        "excluded_automatic_count": {"type": "integer"},
        "excluded_duplicate_count": {"type": "integer"},
        "included_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["title", "date", "category"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "draft",
        "empty",
        "source_count",
        "included_count",
        "excluded_count",
        "excluded_automatic_count",
        "excluded_duplicate_count",
        "included_items",
    ],
    "additionalProperties": False,
}


def build_transform_registry() -> TransformRegistry:
    registry = TransformRegistry()
    registry.register(
        TransformSpec(
            name=WORK_ITEMS_TO_LOG_DRAFT,
            description=(
                "把结构化事项列表去重并转换为可编辑的工作日志草稿；"
                "不推断工时、项目或用户未提供的业务成果。"
            ),
            input_schema=WORK_ITEMS_TO_LOG_DRAFT_INPUT_SCHEMA,
            output_schema=WORK_ITEMS_TO_LOG_DRAFT_OUTPUT_SCHEMA,
            maximum_input_items=100,
            maximum_output_chars=4000,
            halts_on_empty=True,
        ),
        _work_items_to_log_draft,
    )
    return registry


def _work_items_to_log_draft(arguments: dict[str, Any]) -> dict[str, Any]:
    source_items = arguments.get("items") or []
    included: list[dict[str, str]] = []
    seen: set[str] = set()
    automatic_count = 0
    duplicate_count = 0

    for raw in source_items:
        if not isinstance(raw, dict):
            continue
        title = _clean_text(raw.get("title"), maximum=240)
        if not title:
            continue
        category = _clean_text(raw.get("category"), maximum=120)
        date = _clean_text(raw.get("date"), maximum=40)
        if _is_automatic_item(title=title, category=category):
            automatic_count += 1
            continue
        affair_id = _clean_text(raw.get("affair_id"), maximum=256)
        dedupe_key = affair_id or f"{title.casefold()}\n{date}"
        if dedupe_key in seen:
            duplicate_count += 1
            continue
        seen.add(dedupe_key)
        included.append(
            {
                "title": title,
                "date": date,
                "category": category,
            }
        )

    lines: list[str] = []
    for item in included:
        context = "、".join(
            value for value in (item["category"], item["date"]) if value
        )
        suffix = f"（{context}）" if context else ""
        candidate = f"{len(lines) + 1}. 处理《{item['title']}》{suffix}。"
        projected = "\n".join([*lines, candidate])
        if len(projected) > 4000:
            break
        lines.append(candidate)

    if len(lines) < len(included):
        included = included[: len(lines)]
    excluded_count = max(0, len(source_items) - len(included))
    draft = "\n".join(lines)
    return {
        "draft": draft,
        "empty": not bool(draft),
        "source_count": len(source_items),
        "included_count": len(included),
        "excluded_count": excluded_count,
        "excluded_automatic_count": automatic_count,
        "excluded_duplicate_count": duplicate_count,
        "included_items": included,
    }


def _is_automatic_item(*, title: str, category: str) -> bool:
    text = f"{title} {category}".casefold()
    return any(
        marker in text
        for marker in (
            "自动发起",
            "系统通知",
            "自动触发",
        )
    )


def _clean_text(value: Any, *, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]
