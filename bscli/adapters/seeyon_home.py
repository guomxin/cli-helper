from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bscli.adapters.seeyon_write import write_action_risk


_WORKFLOW_OPINION_RE = re.compile(
    r"(?P<handler>\S+)\s+"
    r"(?P<opinion>已阅|同意|不同意|退回|驳回|通过|批准|已处理|阅)\s+"
    r"(?P<time>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})\b"
)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "")

    def has_class(self, class_name: str) -> bool:
        return class_name in self.attr("class").split()

    def text(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text())
        return _clean(" ".join(parts), 20000)

    def descendants(self, include_self: bool = False) -> Iterable["_Node"]:
        if include_self:
            yield self
        for child in self.children:
            yield child
            yield from child.descendants()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].text_parts.append(data)


def parse_pending_list(html: str, *, base_url: str) -> dict:
    root = _parse_html(html)
    section = _find_by_id(root, "section_556815601453123423") or _find_section_by_text(
        root,
        "\u5168\u90e8\u5f85\u529e",
    )
    if section is None:
        return {"count": 0, "items": [], "error": "pending section not found"}

    items = []
    for index, row in enumerate(_find_all(section, tag="tr")):
        cells = [_clean(cell.text(), 300) for cell in _find_all(row, tag="td")]
        compact_cells = [cell for cell in cells if cell]
        link = _find_link(row, onclick_part="checkAndOpenLink") or _first(row, tag="a")
        raw_href = _parse_check_and_open_link(link.attr("onclick") if link else "")
        title_node = _first(row, class_name="titleText")
        title = _clean(
            (title_node.text() if title_node else "")
            or (link.attr("title") if link else "")
            or (compact_cells[0] if compact_cells else "")
            or row.text(),
            500,
        )
        if not title:
            continue
        items.append(
            {
                "index": index,
                "title": title,
                "sender": compact_cells[1] if len(compact_cells) > 1 else "",
                "date": compact_cells[2] if len(compact_cells) > 2 else "",
                "category": compact_cells[3] if len(compact_cells) > 3 else "",
                "affair_id": _query_value(raw_href, "affairId", base_url=base_url),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "read": _has_self_or_descendant_class(row, "AlreadyRead"),
                "raw_text": _clean(row.text(), 800),
            }
        )

    return {"count": len(items), "items": items}


def parse_pending_projection(projection: dict, *, base_url: str) -> dict:
    data = projection.get("Data") if isinstance(projection, dict) else {}
    if not isinstance(data, dict):
        return {"source": "section_api", "count": 0, "items": [], "error": "projection Data not found"}

    items = []
    for index, row in enumerate(data.get("rows") or []):
        cells = row.get("cells") or []
        if not cells:
            continue
        title_cell = cells[0]
        raw_href = title_cell.get("linkURL") or ""
        title = _clean(_strip_html(title_cell.get("cellContentHTML") or ""), 500)
        if not title:
            continue
        items.append(
            {
                "index": index,
                "title": title,
                "sender": _cell_text(cells, 1),
                "date": _cell_text(cells, 2),
                "category": _cell_text(cells, 3),
                "affair_id": _query_value(raw_href, "affairId", base_url=base_url)
                or str(title_cell.get("id") or ""),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "read": title_cell.get("className") != "ReadDifferFromNotRead",
                "raw_text": _clean(" ".join(_cell_text(cells, offset) for offset in range(len(cells))), 800),
            }
        )

    return {
        "source": "section_api",
        "name": projection.get("Name") if isinstance(projection, dict) else "",
        "count": len(items),
        "total": data.get("dataCount"),
        "page": data.get("pageNo"),
        "items": items,
    }


def parse_sent_projection(projection: dict, *, base_url: str) -> dict:
    data = projection.get("Data") if isinstance(projection, dict) else {}
    if not isinstance(data, dict):
        return {"source": "section_api", "count": 0, "items": [], "error": "projection Data not found"}

    items = []
    for index, row in enumerate(data.get("rows") or []):
        cells = row.get("cells") or []
        if not cells:
            continue
        title_cell = cells[0]
        raw_href = title_cell.get("linkURL") or ""
        title = _clean(_strip_html(title_cell.get("cellContentHTML") or ""), 500)
        if not title:
            continue
        items.append(
            {
                "index": index,
                "title": title,
                "status": _cell_text(cells, 1),
                "date": _cell_text(cells, 2),
                "category": _cell_text(cells, 3),
                "affair_id": _query_value(raw_href, "affairId", base_url=base_url)
                or str(title_cell.get("id") or ""),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "raw_text": _clean(" ".join(_cell_text(cells, offset) for offset in range(len(cells))), 800),
            }
        )

    return {
        "source": "section_api",
        "name": projection.get("Name") if isinstance(projection, dict) else "",
        "count": len(items),
        "total": data.get("dataCount"),
        "page": data.get("pageNo"),
        "items": items,
    }


def parse_template_projection(projection: dict, *, base_url: str) -> dict:
    data = projection.get("Data") if isinstance(projection, dict) else {}
    if not isinstance(data, dict):
        return {"source": "section_api", "count": 0, "items": [], "error": "projection Data not found"}

    items = []
    for index, item in enumerate(data.get("items") or []):
        raw_href = item.get("link") or item.get("linkURL") or ""
        title = _clean(
            _strip_html(item.get("title") or item.get("name") or item.get("subjectHTML") or ""),
            500,
        )
        if not title:
            continue
        items.append(
            {
                "index": index,
                "title": title,
                "template_id": _query_value(raw_href, "templateId", base_url=base_url)
                or str(item.get("id") or item.get("optionId") or ""),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "raw_href": raw_href,
                "open_type": str(item.get("openType") or ""),
                "raw_text": title,
            }
        )

    for row_index, row in enumerate(data.get("rows") or []):
        cells = row.get("cells") or []
        if not cells:
            continue
        title_cell = cells[0]
        cell_html = title_cell.get("cellContentHTML") or ""
        raw_href = title_cell.get("linkURL") or _parse_open_data_link(cell_html)
        title = _clean(
            _strip_html(cell_html)
            or title_cell.get("alt")
            or title_cell.get("title")
            or title_cell.get("name")
            or "",
            500,
        )
        if not title:
            continue
        items.append(
            {
                "index": len(items),
                "title": title,
                "template_id": _query_value(raw_href, "templateId", base_url=base_url)
                or str(title_cell.get("id") or ""),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "raw_href": raw_href,
                "raw_text": _clean(_strip_html(cell_html), 800),
            }
        )

    return {
        "source": "section_api",
        "name": projection.get("Name") if isinstance(projection, dict) else "",
        "count": len(items),
        "total": data.get("dataCount") if data.get("dataCount") is not None else data.get("dataNum"),
        "page": data.get("pageNo"),
        "items": items,
    }


def parse_template_list(html: str, *, base_url: str) -> dict:
    root = _parse_html(html)
    section = _find_by_id(root, "section_-6503951670357636432") or _find_section_by_text(
        root,
        "\u6211\u7684\u6a21\u677f",
    )
    if section is None:
        return {"count": 0, "items": [], "error": "template section not found"}

    items = []
    for index, table in enumerate(
        node
        for node in section.descendants()
        if node.tag == "table" and node.has_class("chessboardtable")
    ):
        link = _first(table, tag="a")
        clickable = _find_clickable(table) or link or table
        raw_href = _parse_open_data_link(clickable.attr("onclick"))
        title = _clean(
            table.attr("title")
            or (link.attr("title") if link else "")
            or (link.text() if link else "")
            or table.text(),
            500,
        )
        if not title:
            continue
        items.append(
            {
                "index": index,
                "title": title,
                "template_id": _query_value(raw_href, "templateId", base_url=base_url),
                "href": _join_app_url(base_url, raw_href) if raw_href else "",
                "raw_href": raw_href,
                "raw_text": _clean(table.text(), 800),
            }
        )

    return {"count": len(items), "items": items}


def parse_navigation_inventory(html: str, *, base_url: str) -> dict:
    root = _parse_html(html)
    portals = _parse_portals(root)
    shortcuts = _parse_shortcuts(root, base_url=base_url)
    sections = _parse_sections(root)
    return {
        "portal_count": len(portals),
        "portals": portals,
        "shortcut_count": len(shortcuts),
        "shortcuts": shortcuts,
        "section_count": len(sections),
        "sections": sections,
    }


def parse_oa_detail(html: str, *, base_url: str) -> dict:
    root = _parse_html(html)
    title = _detail_title(root)
    text = _clean(root.text(), 20000)
    fields = _parse_detail_fields(root)
    attachments = _parse_detail_attachments(root, base_url=base_url)
    workflow = _parse_detail_workflow(root)
    actions = _parse_detail_write_actions(html)
    return {
        "title": title,
        "url": base_url,
        "text": text,
        "fields": fields,
        "attachments": attachments,
        "attachment_count": len(attachments),
        "workflow": workflow,
        "workflow_count": len(workflow),
        "actions": actions,
        "action_count": len(actions),
        "write_hints": _parse_detail_write_hints(root, html, base_url=base_url),
    }


def _detail_title(root: _Node) -> str:
    for node in root.descendants():
        if node.tag in {"h1", "h2"}:
            title = _clean(node.text(), 300)
            if title:
                return title
    for element_id in ("summarySubject", "subject", "title"):
        node = _find_by_id(root, element_id)
        if node:
            title = _clean(node.text() or node.attr("title"), 300)
            if title:
                return title
    title_node = _first(root, tag="title")
    return _clean(title_node.text(), 300) if title_node else ""


def _parse_detail_fields(root: _Node) -> list[dict]:
    fields = []
    seen = set()
    for row in _find_all(root, tag="tr"):
        row_marker = " ".join(row.attr(name).lower() for name in ("id", "class", "name") if row.attr(name))
        if any(marker in row_marker for marker in ("workflow", "process", "opinion", "processlog")):
            continue
        cells = [
            _clean(cell.text(), 500)
            for cell in row.children
            if cell.tag in {"th", "td"}
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        name = _field_name(cells[0])
        value = _clean(" ".join(cells[1:]), 1000)
        if name.lower() in {"node", "opinion", "workflow", "process"}:
            continue
        if not name or not value:
            continue
        key = (name, value)
        if key in seen:
            continue
        seen.add(key)
        fields.append({"name": name, "value": value})
        if len(fields) >= 200:
            break
    return fields


def _field_name(value: str) -> str:
    return _clean(str(value or "").rstrip(":："), 200)


def _parse_detail_attachments(root: _Node, *, base_url: str) -> list[dict]:
    attachments = []
    seen = set()
    for link in _find_all(root, tag="a"):
        href = link.attr("href").replace("&amp;", "&")
        name = _clean(link.attr("title") or link.text() or href.rsplit("/", 1)[-1], 300)
        if not href or not _looks_like_attachment(href, name):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        attachments.append(
            {
                "name": name,
                "href": absolute,
                "raw_href": href,
            }
        )
    return attachments


def _looks_like_attachment(href: str, name: str) -> bool:
    value = f"{href} {name}".lower()
    if any(marker in value for marker in ("download", "fileupload", "attachment", "fileid=", "createfile.do")):
        return True
    return bool(re.search(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|csv|png|jpe?g)(\?|$)", value))


def _parse_detail_workflow(root: _Node) -> list[dict]:
    workflow = []
    seen = set()
    extra_keywords = ("意见", "流程", "节点", "处理", "审批", "已阅")
    keywords = ("意见", "流程", "节点", "处理", "审批", "opinion", "workflow", "process", "approved", "approval")
    for node in root.descendants():
        if node.tag in {"html", "body", "document"}:
            continue
        if node.tag not in {"tr", "li", "div", "section", "p"}:
            continue
        marker = " ".join(
            node.attr(name).lower()
            for name in ("id", "class", "name")
            if node.attr(name)
        )
        text = _clean(node.text(), 1000)
        if not text:
            continue
        haystack = f"{marker} {text}".lower()
        if not any(keyword.lower() in haystack for keyword in (*keywords, *extra_keywords)):
            continue
        for entry in _clean_workflow_entries(text):
            key = _workflow_entry_key(entry)
            if key in seen:
                continue
            seen.add(key)
            workflow.append(entry)
            if len(workflow) >= 100:
                break
        if len(workflow) >= 100:
            break
    return workflow


def _clean_workflow_entries(text: str) -> list[dict]:
    text = _clean(text, 1000)
    if not text or _looks_like_workflow_noise(text):
        return []
    text = _strip_workflow_ui_tail(text)
    if not text or _looks_like_workflow_noise(text):
        return []
    structured = [_workflow_entry_from_match(match) for match in _WORKFLOW_OPINION_RE.finditer(text)]
    if structured:
        return structured
    return [{"text": text}]


def _clean_workflow_entry(text: str) -> dict | None:
    entries = _clean_workflow_entries(text)
    return entries[0] if entries else None


def _parse_workflow_entry_fields(text: str) -> dict[str, str]:
    match = _WORKFLOW_OPINION_RE.match(text)
    if not match:
        return {}
    return _workflow_fields_from_match(match)


def _workflow_entry_from_match(match: re.Match) -> dict[str, str]:
    fields = _workflow_fields_from_match(match)
    return {
        "text": f"{fields['handler']} {fields['opinion']} {fields['time']}",
        **fields,
    }


def _workflow_fields_from_match(match: re.Match) -> dict[str, str]:
    return {
        "handler": match.group("handler"),
        "opinion": match.group("opinion"),
        "time": match.group("time"),
    }


def _workflow_entry_key(entry: dict) -> tuple:
    if all(entry.get(key) for key in ("handler", "opinion", "time")):
        return (entry.get("handler"), entry.get("opinion"), entry.get("time"))
    return (entry.get("text", ""),)


def _looks_like_workflow_noise(text: str) -> bool:
    compact = _clean(text, 1000)
    lower = compact.lower()
    code_markers = (
        "<!--",
        "{{",
        "function ",
        "var ",
        "jsonarrbase",
        "document.",
        "return false",
        "$.",
        "$(",
        ".css(",
    )
    if any(marker in lower for marker in code_markers):
        return True
    header_markers = (
        "处理人意见区",
        "与我相关",
        "意见隐藏",
        "显示更多意见",
        "不包括:",
        "不包括：",
        "关联文档",
        "明细日志",
        "流程最大化",
    )
    has_timestamp = bool(re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}", compact))
    has_english_opinion = bool(re.search(r"\b(opinion|approved|approval|rejected|reject)\b", lower))
    if any(marker in compact for marker in header_markers):
        return True
    chinese_opinion_markers = (
        "已阅",
        "同意",
        "不同意",
        "退回",
        "驳回",
        "通过",
        "批准",
        "已处理",
        "阅",
    )
    has_chinese_opinion = has_timestamp and any(marker in compact for marker in chinese_opinion_markers)
    if not has_english_opinion and not has_chinese_opinion:
        return True
    return False


def _strip_workflow_ui_tail(text: str) -> str:
    text = re.sub(r"\s+暂无数据\s*$", "", text)
    text = re.sub(r"\s+回复\s*\(\s*\)\s*\d+\s*$", "", text)
    return _clean(text, 1000)


def _parse_detail_write_actions(html: str) -> list[dict]:
    actions = []
    seen = set()
    for item in _extract_json_arr_base_items(html):
        if not isinstance(item, dict):
            continue
        codes = item.get("codes") if isinstance(item.get("codes"), list) else []
        code = str((codes[0] if codes else item.get("id")) or "").strip()
        label = _clean(str(item.get("label") or code), 100)
        if not code and not label:
            continue
        key = (code, label)
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            {
                "code": code,
                "label": label,
                "id": str(item.get("id") or code),
                "access": "write",
                "risk": write_action_risk(code, label),
                "requires_confirmation": True,
                "supports_dry_run": True,
                "source": "jsonArrBase",
            }
        )
    return actions


def _extract_json_arr_base_items(html: str) -> list:
    items = []
    for match in re.finditer(r"jsonArrBase\s*=\s*'((?:\\'|[^'])*)'", str(html or "")):
        raw_json = match.group(1).replace("\\'", "'").replace('\\"', '"')
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, list):
            items.extend(decoded)
    return items


def _parse_detail_write_hints(root: _Node, html: str, *, base_url: str) -> dict:
    csrf_tokens = []
    if re.search(r"\bCSRFTOKEN\b\s*=", str(html or "")):
        csrf_tokens.append({"name": "CSRFTOKEN", "value_present": True})
    hidden_fields = []
    seen = set()
    for node in root.descendants():
        if node.tag != "input" or node.attr("type").lower() != "hidden":
            continue
        name = node.attr("name") or node.attr("id")
        if not name or name in seen:
            continue
        seen.add(name)
        hidden_fields.append({"name": name, "value_present": bool(node.attr("value"))})
        if len(hidden_fields) >= 100:
            break
    hints = {"csrf_tokens": csrf_tokens, "hidden_fields": hidden_fields}
    endpoint_candidates = _parse_write_endpoint_candidates(str(html or ""), base_url=base_url)
    if endpoint_candidates:
        hints["endpoint_candidates"] = endpoint_candidates
    return hints


def _parse_write_endpoint_candidates(html: str, *, base_url: str) -> list[dict]:
    candidates = []
    seen = set()
    for match in re.finditer(r"['\"]([^'\"]+\.do\?method=[^'\"]+)['\"]", html):
        raw_url = match.group(1).replace("&amp;", "&")
        if not _looks_like_write_endpoint(raw_url):
            continue
        absolute = _join_app_url(base_url, raw_url)
        if absolute in seen:
            continue
        seen.add(absolute)
        candidates.append(
            {
                "url": absolute,
                "method": "UNKNOWN",
                "risk": "high",
                "source": "rendered_html",
                "tested": False,
            }
        )
        if len(candidates) >= 50:
            break
    return candidates


def _looks_like_write_endpoint(url: str) -> bool:
    parsed = urlparse(_join_app_url("http://localhost/seeyon/main.do?method=main", str(url or "")))
    method_values = parse_qs(parsed.query, keep_blank_values=True).get("method", [])
    value = f"{parsed.path} {' '.join(method_values)}".lower()
    return any(
        marker in value
        for marker in (
            "submit",
            "finish",
            "save",
            "archive",
            "delete",
            "revoke",
            "cancel",
            "upload",
            "opinion",
            "workitem",
        )
    )


def _parse_portals(root: _Node) -> list[dict]:
    portals = []
    for index, node in enumerate(
        node
        for node in root.descendants()
        if node.tag == "li" and node.attr("id").startswith("spaceLi_")
    ):
        name = _clean(node.attr("title") or node.text(), 100)
        if not name:
            continue
        portals.append(
            {
                "index": index,
                "name": name,
                "portal_id": node.attr("id").removeprefix("spaceLi_"),
                "navigation_index": _parse_navigation_index(node.attr("onclick")),
                "active": node.has_class("current"),
                "onclick": node.attr("onclick"),
            }
        )
    return portals


def _parse_shortcuts(root: _Node, *, base_url: str) -> list[dict]:
    shortcuts = []
    for index, node in enumerate(
        node
        for node in root.descendants()
        if node.has_class("lev1Title")
        and node.has_class("navTitleName")
        and "onSeeyonTopNavMenuClick" in node.attr("onclick")
    ):
        args = _parse_single_quoted_args(node.attr("onclick"))
        raw_href = args[0] if len(args) > 0 else ""
        target = args[2] if len(args) > 2 else ""
        name = _clean(node.attr("title") or node.text(), 100)
        if not name:
            continue
        shortcuts.append(
            {
                "index": index,
                "name": name,
                "menu_id": args[1] if len(args) > 1 else "",
                "target": target,
                "resource_code": args[3] if len(args) > 3 else "",
                "raw_href": raw_href.replace("&amp;", "&"),
                "href": urljoin(base_url, raw_href.replace("&amp;", "&")) if raw_href else "",
                "opens_new_window": target == "newWindow",
                "onclick": node.attr("onclick"),
            }
        )
    return shortcuts


def _parse_sections(root: _Node) -> list[dict]:
    sections = []
    for section in (
        node
        for node in root.descendants()
        if node.attr("id").startswith("section_") and node.attr("id") != "section_"
    ):
        tabs = []
        for index, tab in enumerate(
            node
            for node in section.descendants()
            if node.tag == "li" and node.attr("id").startswith("sectionName_")
        ):
            visible_name, visible_count = _parse_counted_label(tab.text())
            title_name, title_count = _parse_counted_label(tab.attr("title"))
            name = title_name or visible_name
            count = visible_count if visible_count is not None else title_count
            if not name:
                continue
            tabs.append(
                {
                    "index": index,
                    "name": name,
                    "count": count,
                    "tab_id": tab.attr("id").removeprefix("sectionName_"),
                    "active": tab.has_class("current"),
                    "onclick": tab.attr("onclick"),
                }
            )
        if tabs:
            sections.append(
                {
                    "index": len(sections),
                    "section_id": section.attr("id").removeprefix("section_"),
                    "name": tabs[0]["name"],
                    "tabs": tabs,
                }
            )
    return sections


def _parse_html(html: str) -> _Node:
    parser = _TreeBuilder()
    parser.feed(_normalize_html_input(html))
    parser.close()
    return parser.root


def _normalize_html_input(html: str) -> str:
    html = str(html or "")
    stripped = html.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return html
        if isinstance(decoded, str):
            return decoded
    return html


def _clean(value: str, max_length: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def _cell_text(cells: list[dict], index: int) -> str:
    if index >= len(cells):
        return ""
    cell = cells[index] or {}
    return _clean(_strip_html(cell.get("cellContentHTML") or cell.get("alt") or ""), 300)


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or ""))


def _find_by_id(root: _Node, element_id: str) -> _Node | None:
    return next((node for node in root.descendants(include_self=True) if node.attr("id") == element_id), None)


def _find_section_by_text(root: _Node, text: str) -> _Node | None:
    return next(
        (
            node
            for node in root.descendants()
            if node.tag in {"div", "section"}
            and (node.has_class("sectionPanel") or node.attr("id").startswith("section_"))
            and text in node.text()
        ),
        None,
    )


def _find_all(root: _Node, *, tag: str | None = None, class_name: str | None = None) -> list[_Node]:
    return [
        node
        for node in root.descendants()
        if (tag is None or node.tag == tag) and (class_name is None or node.has_class(class_name))
    ]


def _first(root: _Node, *, tag: str | None = None, class_name: str | None = None) -> _Node | None:
    return next(iter(_find_all(root, tag=tag, class_name=class_name)), None)


def _find_link(root: _Node, *, onclick_part: str) -> _Node | None:
    return next(
        (
            node
            for node in root.descendants()
            if node.tag == "a" and onclick_part in node.attr("onclick")
        ),
        None,
    )


def _find_clickable(root: _Node) -> _Node | None:
    return next((node for node in root.descendants() if node.attr("onclick")), None)


def _has_self_or_descendant_class(root: _Node, class_name: str) -> bool:
    return any(node.has_class(class_name) for node in root.descendants(include_self=True))


def _parse_check_and_open_link(onclick: str) -> str:
    match = re.search(r"checkAndOpenLink\('([^']+)'", onclick or "")
    return match.group(1).replace("&amp;", "&") if match else ""


def _parse_open_data_link(onclick: str) -> str:
    match = re.search(r"['\"]url['\"]\s*:\s*['\"]([^'\"]+)", onclick or "")
    return match.group(1).replace("&amp;", "&") if match else ""


def _parse_navigation_index(onclick: str) -> str:
    match = re.search(r"showNavigation\(([^,\)]+)", onclick or "")
    return match.group(1).strip("'\" ") if match else ""


def _parse_single_quoted_args(source: str) -> list[str]:
    return [match.replace("&amp;", "&") for match in re.findall(r"'([^']*)'", source or "")]


def _parse_counted_label(label: str) -> tuple[str, int | None]:
    label = _clean(label, 100)
    match = re.match(r"^(.*?)[(（](\d+)[)）]$", label)
    if not match:
        return label, None
    return _clean(match.group(1), 100), int(match.group(2))


def _query_value(url: str, key: str, *, base_url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(_join_app_url(base_url, url))
    values = parse_qs(parsed.query, keep_blank_values=True).get(key)
    if values:
        return values[0]
    match = re.search(rf"[?&]{re.escape(key)}=([^&]+)", url)
    return unquote(match.group(1)) if match else ""


def _join_app_url(base_url: str, url: str) -> str:
    url = str(url or "").replace("&amp;", "&")
    if not url:
        return ""
    base = urlparse(base_url)
    if (
        base.path.startswith("/seeyon/")
        and url.startswith("/")
        and not url.startswith("/seeyon/")
        and not url.startswith("//")
    ):
        return urljoin(base_url, f"/seeyon{url}")
    return urljoin(base_url, url)
