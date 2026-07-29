from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlparse

from bscli.adapters.base import (
    AdapterLoginContractMismatch,
    AdapterLoginRequired,
    AdapterSessionCheckUnavailable,
)
from bscli.core.capability import CapabilityRegistry, CapabilitySpec


YUQUE_SYSTEM_ID = "yuque"
YUQUE_ADAPTER_ID = "yuque-central"
YUQUE_SYSTEM_NAME = "部门信息库"

YUQUE_PUBLIC_BOOKS_CAPABILITY = "yuque.public_books.list"
YUQUE_DOCUMENT_CATALOG_CAPABILITY = "yuque.document.catalog"
YUQUE_DOCUMENT_SEARCH_CAPABILITY = "yuque.document.search"
YUQUE_DOCUMENT_READ_CAPABILITY = "yuque.document.read"

_BOOK_SELECTOR_SCHEMA = {
    "book": {
        "type": "string",
        "description": "Knowledge-base name, slug, or numeric id. Defaults to 共享文档.",
    },
}

_SECRET_PATTERNS = (
    (
        "credential",
        re.compile(
            r"(?im)(账号|用户名|user(?:name)?|密码|口令|password|passwd)"
            r"(\s*[:：=]\s*)([^\s,，;；<>{}\[\]]{2,})"
        ),
    ),
    (
        "token",
        re.compile(
            r"(?im)(access[_ -]?key(?:[_ -]?id)?|secret(?:[_ -]?key)?|"
            r"api[_ -]?key|token|bearer)"
            r"(\s*[:：=]\s*)([A-Za-z0-9_./+=-]{6,})"
        ),
    ),
    (
        "url_credential",
        re.compile(r"(?i)\b(https?://)([^/\s:@]+):([^@\s/]+)@"),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
)


class YuqueLoginRequired(AdapterLoginRequired):
    pass


class YuqueLoginContractMismatch(AdapterLoginContractMismatch):
    pass


class YuqueSessionCheckUnavailable(AdapterSessionCheckUnavailable):
    pass


class YuqueCentralAdapter:
    def __init__(self, *, base_url: str, organization_id: int) -> None:
        parsed = urlparse(str(base_url or ""))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Yuque base URL must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Yuque base URL is invalid")
        if parsed.path not in {"", "/"}:
            raise ValueError("Yuque base URL must not include a path")
        hostname = (parsed.hostname or "").lower()
        if hostname != "yuque.com" and not hostname.endswith(".yuque.com"):
            raise ValueError("Yuque base URL must use a yuque.com host")
        if not isinstance(organization_id, int) or organization_id <= 0:
            raise ValueError("Yuque organization id must be a positive integer")
        self.base_url = f"https://{parsed.netloc.lower()}/"
        self.origin = f"https://{parsed.netloc.lower()}"
        self.organization_id = organization_id
        self.organization_login = hostname.removesuffix(".yuque.com")

    def authentication_contract(self) -> dict:
        return {
            "system_id": YUQUE_SYSTEM_ID,
            "system_name": YUQUE_SYSTEM_NAME,
            "origin": self.origin,
            "page_fingerprint": "yuque-interactive-login-v1",
            "authentication_mode": "interactive_browser",
            "fields": [],
            "interactive": {
                "entry_url": self._url(
                    "/login?"
                    + urlencode(
                        {
                            "org": self.organization_login,
                            "goto": self.base_url,
                        }
                    )
                ),
                "viewport": {"width": 430, "height": 760},
                "requires_human_verification": True,
            },
        }

    def begin_interactive_login(self, worker, *, timeout_seconds: float) -> dict:
        entry_url = self.authentication_contract()["interactive"]["entry_url"]
        worker.goto(entry_url, timeout_seconds=timeout_seconds)
        return {"url": worker.page_url, "transport": "central_interactive_browser"}

    def authenticate(self, _worker, _credentials: dict, *, timeout_seconds: float) -> dict:
        del timeout_seconds
        raise YuqueLoginContractMismatch(
            "Yuque requires the interactive browser authentication contract."
        )

    def probe_session(self, worker) -> dict:
        payload = self._request_json(worker, "GET", "/api/mine", login_probe=True)
        principal = payload.get("data")
        if not isinstance(principal, dict):
            raise YuqueLoginContractMismatch("Yuque session response has no principal.")
        observed = str(
            principal.get("publicName")
            or principal.get("name")
            or principal.get("login")
            or ""
        ).strip()
        if not observed:
            raise YuqueLoginContractMismatch(
                "Yuque session response has no verifiable principal."
            )
        if principal.get("isInCurrentOrg") is False:
            raise YuqueLoginRequired(
                "The authenticated Yuque account is not a member of this organization."
            )
        return {
            "authenticated": True,
            "observed_principal_ref": observed,
            "principal": {
                "id": str(principal.get("id") or ""),
                "login": str(principal.get("login") or ""),
                "name": observed,
                "organization_member": principal.get("isInCurrentOrg"),
            },
            "template_count": None,
            "transport": "central_browser_cookie",
        }

    def invoke_capability(self, capability_name: str, worker, arguments: dict) -> dict:
        if capability_name == YUQUE_PUBLIC_BOOKS_CAPABILITY:
            return self.list_public_books(worker)
        if capability_name == YUQUE_DOCUMENT_CATALOG_CAPABILITY:
            return self.list_documents(worker, arguments)
        if capability_name == YUQUE_DOCUMENT_SEARCH_CAPABILITY:
            return self.search_documents(worker, arguments)
        if capability_name == YUQUE_DOCUMENT_READ_CAPABILITY:
            return self.read_document(worker, arguments)
        raise KeyError(f"unsupported Yuque capability: {capability_name}")

    def list_public_books(self, worker) -> dict:
        public_area, books = self._public_area(worker)
        return {
            "publicArea": public_area,
            "count": len(books),
            "items": books,
        }

    def list_documents(self, worker, arguments: dict) -> dict:
        book = self._resolve_book(worker, arguments.get("book"))
        payload = self._request_json(
            worker,
            "GET",
            f"/api/docs?{urlencode({'book_id': book['id']})}",
        )
        raw_items = payload.get("data")
        if not isinstance(raw_items, list):
            raise YuqueSessionCheckUnavailable(
                "Yuque document catalog did not return a list."
            )
        keyword = str(arguments.get("keyword") or "").strip()
        normalized = [
            _normalize_document_summary(item, book)
            for item in raw_items
            if isinstance(item, dict)
        ]
        if keyword:
            folded = keyword.casefold()
            normalized = [
                item
                for item in normalized
                if folded in item["title"].casefold()
            ]
        limit = _bounded_int(arguments.get("limit"), default=100, minimum=1, maximum=500)
        return {
            "book": book,
            "keyword": keyword or None,
            "total": len(normalized),
            "count": min(len(normalized), limit),
            "items": normalized[:limit],
        }

    def search_documents(self, worker, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        book = self._resolve_book(worker, arguments.get("book"))
        public_area, _books = self._public_area(worker)
        page = _bounded_int(arguments.get("page"), default=1, minimum=1, maximum=1000)
        limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=50)
        scope = f"{public_area['login']}/{book['slug']}"
        params = {
            "p": page,
            "q": query,
            "limit": limit,
            "sence": "modal",
            "type": "content",
            "scope": scope,
            "tab": "book",
        }
        payload = self._request_json(
            worker,
            "GET",
            f"/api/zsearch?{urlencode(params)}",
        )
        result = payload.get("data")
        if not isinstance(result, dict) or not isinstance(result.get("hits"), list):
            raise YuqueSessionCheckUnavailable(
                "Yuque search did not return a hit list."
            )
        items = [
            _normalize_search_hit(item, book)
            for item in result["hits"]
            if isinstance(item, dict)
        ]
        return {
            "query": query,
            "book": book,
            "page": page,
            "count": len(items),
            "total": int(result.get("totalHits") or result.get("numHits") or len(items)),
            "snippetPolicy": "omitted_to_prevent_incidental_secret_disclosure",
            "items": items,
        }

    def read_document(self, worker, arguments: dict) -> dict:
        selector = str(arguments.get("document") or "").strip()
        if not selector:
            raise ValueError("document is required")
        book = self._resolve_book(worker, arguments.get("book"))
        document = self._resolve_document(worker, book, selector)
        params = {
            "include_contributors": "true",
            "include_like": "true",
            "include_hits": "true",
            "merge_dynamic_data": "false",
            "book_id": book["id"],
        }
        payload = self._request_json(
            worker,
            "GET",
            f"/api/docs/{quote(document['slug'], safe='')}?{urlencode(params)}",
        )
        raw = payload.get("data")
        if not isinstance(raw, dict):
            raise YuqueSessionCheckUnavailable("Yuque document response is invalid.")
        plain_text = lake_to_plain_text(str(raw.get("content") or ""))
        sanitized_text, redactions = redact_sensitive_text(plain_text)
        max_chars = _bounded_int(
            arguments.get("max_chars"),
            default=12_000,
            minimum=500,
            maximum=50_000,
        )
        truncated = len(sanitized_text) > max_chars
        if truncated:
            sanitized_text = sanitized_text[:max_chars].rstrip()
        return {
            "document": {
                **_normalize_document_summary(raw, book),
                "author": _normalize_person(raw.get("user")),
                "lastEditor": _normalize_person(raw.get("last_editor")),
                "contributors": [
                    person
                    for item in raw.get("contributors") or []
                    if isinstance(item, dict)
                    if (person := _normalize_person(item)) is not None
                ],
                "wordCount": raw.get("word_count"),
                "contentUpdatedAt": raw.get("content_updated_at"),
                "updatedAt": raw.get("updated_at"),
            },
            "content": sanitized_text,
            "contentFormat": "plain_text_from_lake",
            "truncated": truncated,
            "maxChars": max_chars,
            "redaction": {
                "applied": bool(redactions),
                "count": len(redactions),
                "categories": sorted(set(redactions)),
                "policy": "likely credentials and secrets are never returned to the agent",
            },
        }

    def _resolve_book(self, worker, selector: Any) -> dict:
        _public_area, books = self._public_area(worker)
        query = str(selector or "共享文档").strip()
        exact = [
            book
            for book in books
            if query == str(book["id"])
            or query.casefold() == book["slug"].casefold()
            or query.casefold() == book["name"].casefold()
        ]
        if len(exact) == 1:
            return exact[0]
        partial = [book for book in books if query.casefold() in book["name"].casefold()]
        if len(partial) == 1:
            return partial[0]
        if not exact and not partial:
            raise ValueError(f"Yuque knowledge base was not found: {query}")
        candidates = exact or partial
        raise ValueError(
            "Yuque knowledge-base selector is ambiguous: "
            + ", ".join(book["name"] for book in candidates[:10])
        )

    def _resolve_document(self, worker, book: dict, selector: str) -> dict:
        catalog = self.list_documents(
            worker,
            {"book": str(book["id"]), "limit": 500},
        )["items"]
        folded = selector.casefold()
        exact = [
            item
            for item in catalog
            if selector == str(item["id"])
            or folded == item["slug"].casefold()
            or folded == item["title"].casefold()
        ]
        if len(exact) == 1:
            return exact[0]
        partial = [item for item in catalog if folded in item["title"].casefold()]
        if len(partial) == 1:
            return partial[0]
        if not exact and not partial:
            raise ValueError(f"Yuque document was not found: {selector}")
        candidates = exact or partial
        raise ValueError(
            "Yuque document selector is ambiguous: "
            + ", ".join(item["title"] for item in candidates[:10])
        )

    def _public_area(self, worker) -> tuple[dict, list[dict]]:
        payload = self._request_json(
            worker,
            "GET",
            "/api/modules/org_wiki/wiki/show?"
            + urlencode({"organizationId": self.organization_id}),
        )
        wiki = payload.get("wiki")
        layouts = payload.get("layouts")
        if not isinstance(wiki, dict) or not isinstance(layouts, list):
            raise YuqueSessionCheckUnavailable(
                "Yuque public-area response is invalid."
            )
        books: list[dict] = []
        for layout in layouts:
            if not isinstance(layout, dict):
                continue
            for placement in layout.get("placements") or []:
                if not isinstance(placement, dict):
                    continue
                for block in placement.get("blocks") or []:
                    if not isinstance(block, dict) or block.get("type") != "bookStacks":
                        continue
                    for stack in block.get("data") or []:
                        if not isinstance(stack, dict):
                            continue
                        for book in stack.get("books") or []:
                            if isinstance(book, dict):
                                books.append(_normalize_book(book))
        public_area = {
            "id": str(wiki.get("id") or ""),
            "name": str(wiki.get("name") or "公共区"),
            "login": str(wiki.get("login") or ""),
            "organizationId": self.organization_id,
        }
        if not public_area["login"]:
            raise YuqueLoginContractMismatch(
                "Yuque public area has no stable login identifier."
            )
        return public_area, books

    def _request_json(
        self,
        worker,
        method: str,
        path: str,
        *,
        login_probe: bool = False,
    ) -> dict:
        response = worker.request(method, self._url(path))
        final_url = str(response.get("url") or "")
        if response["status"] in {401, 403} or "/login" in urlparse(final_url).path:
            raise YuqueLoginRequired("Yuque login is missing or expired.")
        if response["status"] < 200 or response["status"] >= 300:
            if login_probe and response["status"] in {302, 303, 307, 308}:
                raise YuqueLoginRequired("Yuque login is missing or expired.")
            raise YuqueSessionCheckUnavailable(
                f"Yuque API returned HTTP {response['status']}."
            )
        payload = response.get("json")
        if not isinstance(payload, dict):
            raise YuqueSessionCheckUnavailable("Yuque API did not return JSON.")
        return payload

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))


def build_yuque_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    specs = (
        CapabilitySpec(
            name=YUQUE_PUBLIC_BOOKS_CAPABILITY,
            version="0.1.0",
            description="List knowledge bases in the authenticated Yuque public area.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object"},
            effect="read",
            adapter=YUQUE_ADAPTER_ID,
            workflow="yuque-public-books-v1",
        ),
        CapabilitySpec(
            name=YUQUE_DOCUMENT_CATALOG_CAPABILITY,
            version="0.1.0",
            description="List or filter documents in one Yuque knowledge base.",
            input_schema={
                "type": "object",
                "properties": {
                    **_BOOK_SELECTOR_SCHEMA,
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=YUQUE_ADAPTER_ID,
            workflow="yuque-document-catalog-v1",
        ),
        CapabilitySpec(
            name=YUQUE_DOCUMENT_SEARCH_CAPABILITY,
            version="0.1.0",
            description=(
                "Search Yuque document content while omitting snippets that may "
                "incidentally expose credentials."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    **_BOOK_SELECTOR_SCHEMA,
                    "query": {"type": "string"},
                    "page": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=YUQUE_ADAPTER_ID,
            workflow="yuque-document-search-v1",
        ),
        CapabilitySpec(
            name=YUQUE_DOCUMENT_READ_CAPABILITY,
            version="0.1.0",
            description=(
                "Read one explicitly selected Yuque document as sanitized plain text."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    **_BOOK_SELECTOR_SCHEMA,
                    "document": {
                        "type": "string",
                        "description": "Document title, slug, or numeric id.",
                    },
                    "max_chars": {"type": "integer"},
                },
                "required": ["document"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            effect="read",
            adapter=YUQUE_ADAPTER_ID,
            workflow="yuque-document-read-v1",
        ),
    )
    for spec in specs:
        registry.register(spec)
    return registry


def lake_to_plain_text(content: str) -> str:
    parser = _LakeTextParser()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", content)
    else:
        text = parser.text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def redact_sensitive_text(text: str) -> tuple[str, list[str]]:
    categories: list[str] = []
    value = text
    for category, pattern in _SECRET_PATTERNS:
        if category == "url_credential":
            value, count = pattern.subn(r"\1[REDACTED]@", value)
        elif category == "private_key":
            value, count = pattern.subn("[REDACTED PRIVATE KEY]", value)
        else:
            value, count = pattern.subn(r"\1\2[REDACTED]", value)
        categories.extend([category] * count)
    return value, categories


class _LakeTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and lowered in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and lowered in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _normalize_book(book: dict) -> dict:
    return {
        "id": str(book.get("id") or ""),
        "slug": str(book.get("slug") or ""),
        "name": str(book.get("name") or ""),
        "description": str(book.get("description") or ""),
        "documentCount": int(book.get("items_count") or 0),
        "updatedAt": book.get("content_updated_at") or book.get("updated_at"),
    }


def _normalize_document_summary(item: dict, book: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "slug": str(item.get("slug") or ""),
        "title": str(item.get("title") or ""),
        "type": str(item.get("type") or ""),
        "book": {"id": book["id"], "slug": book["slug"], "name": book["name"]},
        "wordCount": item.get("word_count"),
        "updatedAt": item.get("content_updated_at") or item.get("updated_at"),
    }


def _normalize_search_hit(item: dict, book: dict) -> dict:
    return {
        "id": str(item.get("id") or ""),
        "slug": str(item.get("slug") or ""),
        "title": str(item.get("title") or ""),
        "type": str(item.get("type") or ""),
        "book": {
            "id": book["id"],
            "slug": book["slug"],
            "name": str(item.get("book_name") or book["name"]),
        },
        "url": str(item.get("url") or ""),
        "snippet": None,
    }


def _normalize_person(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    name = str(
        value.get("publicName") or value.get("name") or value.get("login") or ""
    ).strip()
    if not name:
        return None
    return {
        "id": str(value.get("id") or ""),
        "login": str(value.get("login") or ""),
        "name": name,
    }


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric option is invalid") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"numeric option must be between {minimum} and {maximum}")
    return parsed
