from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlencode, urljoin

from bscli.adapters.base import AdapterLoginRequired


DOCUMENT_CERTIFICATE_SEARCH_CAPABILITY = "oa.document.certificate.search"

DOCUMENT_CERTIFICATE_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 10,
        },
        "document_type": {
            "type": "string",
            "enum": [
                "all",
                "patent_certificate",
                "software_copyright_certificate",
            ],
        },
        "limit": {"type": "integer"},
    },
    "additionalProperties": False,
}

_DOCUMENT_CENTER_LABEL = "\u6587\u6863\u4e2d\u5fc3"
_UNIT_DOCUMENTS_LABEL = "\u5355\u4f4d\u6587\u6863"
_INTELLECTUAL_PROPERTY_LABEL = "\u77e5\u8bc6\u4ea7\u6743\u6587\u6863"
_GROUP_CERTIFICATES_LABEL = "02\u96c6\u56e2-\u8bc1\u4e66\u626b\u63cf\u4ef6"
_ALL_APPS_LABEL = "\u5168\u90e8\u5e94\u7528"
_APP_SEARCH_PLACEHOLDER = "\u8bf7\u8f93\u5165\u5173\u952e\u5b57\u641c\u7d22"

_CERTIFICATE_CATEGORIES = {
    "patent_certificate": "1-\u4e13\u5229\u8bc1\u4e66\u626b\u63cf\u4ef6",
    "software_copyright_certificate": "2-\u8457\u4f5c\u6743\u8bc1\u4e66\u626b\u63cf\u4ef6",
}

_RESULT_SETTLE_MS = 900
_MAX_DOCUMENT_BYTES = 32 * 1024 * 1024


class SeeyonDocumentContractMismatch(RuntimeError):
    pass


class SeeyonDocumentAccessDenied(RuntimeError):
    pass


def search_certificate_documents(
    worker,
    *,
    base_url: str,
    arguments: dict,
) -> dict:
    queries = _validated_queries(arguments)
    document_type = str(arguments.get("document_type") or "all").strip()
    if document_type == "all":
        requested_types = tuple(_CERTIFICATE_CATEGORIES)
    elif document_type in _CERTIFICATE_CATEGORIES:
        requested_types = (document_type,)
    else:
        raise ValueError("document_type is invalid")
    limit = _validated_limit(arguments.get("limit"))

    matches: list[dict] = []
    inaccessible_count = 0
    for current_type in requested_types:
        frame = _open_certificate_category(
            worker,
            base_url=base_url,
            category_label=_CERTIFICATE_CATEGORIES[current_type],
        )
        for query_index, query in enumerate(queries):
            rows = _search_current_folder(worker, frame=frame, query=query)
            for row in rows:
                if not _row_matches_query(row, query):
                    continue
                public_item = _public_document_item(row, current_type, query)
                public_item["query"] = query
                public_item["_query_index"] = query_index
                if not row["download_acl"] or not row["read_acl"]:
                    inaccessible_count += 1
                    continue
                public_item["_download_reference"] = {
                    "resource_id": row["resource_id"],
                    "source_id": row["source_id"],
                    "filename": row["filename"],
                    "display_size": row["display_size"],
                    "document_type": current_type,
                    "category_label": _CERTIFICATE_CATEGORIES[current_type],
                    "create_date": row["create_date"],
                    "version": row["version"],
                    "mime_type_id": row["mime_type_id"],
                    "secret_level": row["secret_level"],
                    "is_upload_file": row["is_upload_file"],
                }
                matches.append(public_item)

    deduplicated = {}
    for item in matches:
        key = (
            item["_download_reference"]["resource_id"],
            item["document_type"],
        )
        existing = deduplicated.get(key)
        if existing is None or _match_rank(item["match_kind"]) < _match_rank(
            existing["match_kind"]
        ):
            deduplicated[key] = item
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            item["_query_index"],
            _match_rank(item["match_kind"]),
            item["document_type"],
            item["title"].casefold(),
        ),
    )
    source_count = len(ordered)
    ordered = ordered[:limit]
    return {
        "schema_version": "bscli.oa_certificate_search.v2",
        "query": queries[0] if len(queries) == 1 else None,
        "queries": queries,
        "requested_document_type": document_type,
        "scope": "unit_documents/intellectual_property/group_certificates",
        "count": len(ordered),
        "source_count": source_count,
        "inaccessible_count": inaccessible_count,
        "items": [
            {key: value for key, value in item.items() if key != "_query_index"}
            for item in ordered
        ],
    }


def fetch_certificate_document(
    worker,
    *,
    base_url: str,
    reference: dict,
) -> dict:
    reference = _validated_reference(reference)
    frame = _open_certificate_category(
        worker,
        base_url=base_url,
        category_label=reference["category_label"],
    )
    rows = _search_current_folder(
        worker,
        frame=frame,
        query=reference["filename"],
    )
    row = next(
        (
            candidate
            for candidate in rows
            if candidate["resource_id"] == reference["resource_id"]
        ),
        None,
    )
    if row is None:
        raise SeeyonDocumentAccessDenied(
            "the certificate is no longer available to the current OA user"
        )
    immutable_fields = (
        "source_id",
        "filename",
        "create_date",
        "version",
        "mime_type_id",
    )
    if any(str(row[field]) != str(reference[field]) for field in immutable_fields):
        raise SeeyonDocumentAccessDenied(
            "the certificate binding changed after the download link was issued"
        )
    if not row["read_acl"] or not row["download_acl"] or not row["is_upload_file"]:
        raise SeeyonDocumentAccessDenied(
            "the current OA user cannot download this certificate"
        )

    secret_check = frame.evaluate(
        """
        (resourceId) => {
          if (typeof window.callBackendMethod !== 'function') {
            return {available: false, message: ''};
          }
          const value = window.callBackendMethod(
            'secretAjaxManager',
            'checkUserSecretLevel',
            resourceId
          );
          return {available: true, message: String(value || '').trim()};
        }
        """,
        row["resource_id"],
    )
    if not isinstance(secret_check, dict) or not secret_check.get("available"):
        raise SeeyonDocumentContractMismatch(
            "OA certificate security-level check is unavailable"
        )
    if str(secret_check.get("message") or "").strip():
        raise SeeyonDocumentAccessDenied(
            "the current OA user does not satisfy the certificate security level"
        )

    audit_recorded = frame.evaluate(
        """
        (resourceId) => {
          if (typeof window.ajaxRecordOptionLog !== 'function') return false;
          window.ajaxRecordOptionLog(resourceId, 'downLoadFile');
          return true;
        }
        """,
        row["resource_id"],
    )
    if not audit_recorded:
        raise SeeyonDocumentContractMismatch(
            "OA certificate download audit hook is unavailable"
        )

    csrf_suffix = frame.evaluate(
        """
        () => {
          try {
            const topWindow = typeof window.getA8Top === 'function'
              ? window.getA8Top()
              : window.top;
            const guard = topWindow && topWindow.CsrfGuard;
            return guard && typeof guard.getUrlSurffix === 'function'
              ? String(guard.getUrlSurffix() || '')
              : '';
          } catch (_) {
            return '';
          }
        }
        """
    )
    query = urlencode(
        {
            "method": "download",
            "viewMode": "download",
            "fileId": row["source_id"],
            "createDate": row["create_date"],
            "filename": row["filename"],
            "v": row["version"],
        }
    )
    download_url = urljoin(base_url, f"/seeyon/fileDownload.do?{query}")
    if csrf_suffix:
        download_url += (
            csrf_suffix
            if str(csrf_suffix).startswith("&")
            else f"&{str(csrf_suffix).lstrip('?&')}"
        )
    response = _request_download_with_redirects(worker, download_url)
    status = int(response.get("status") or 0)
    body = response.get("body")
    content_type = str(response.get("content_type") or "").split(";", 1)[0].lower()
    if status in {401, 403}:
        raise SeeyonDocumentAccessDenied(
            "OA rejected the certificate download for the current user"
        )
    if status != 200 or not isinstance(body, bytes):
        raise SeeyonDocumentContractMismatch(
            f"OA certificate download returned HTTP {status}"
        )
    if len(body) > _MAX_DOCUMENT_BYTES:
        raise SeeyonDocumentContractMismatch(
            "OA certificate file exceeds the AgentBridge download limit"
        )
    if "html" in content_type or body.lstrip().startswith(b"<!DOCTYPE html"):
        raise AdapterLoginRequired("the central OA session expired during download")
    if not body.startswith(b"%PDF-"):
        raise SeeyonDocumentContractMismatch(
            "OA certificate download did not return a PDF file"
        )
    return {
        "body": body,
        "filename": row["filename"],
        "content_type": "application/pdf",
    }


def _request_download_with_redirects(worker, url: str) -> dict:
    current_url = url
    for _attempt in range(4):
        response = worker.request_bytes(
            "GET",
            current_url,
            timeout_seconds=60,
        )
        status = int(response.get("status") or 0)
        if status not in {301, 302, 303, 307, 308}:
            return response
        location = str(response.get("location") or "").strip()
        if not location:
            raise SeeyonDocumentContractMismatch(
                "OA certificate download redirect has no location"
            )
        current_url = urljoin(str(response.get("url") or current_url), location)
    raise SeeyonDocumentContractMismatch(
        "OA certificate download exceeded the redirect limit"
    )

def _open_certificate_category(
    worker,
    *,
    base_url: str,
    category_label: str,
):
    page = worker.goto(base_url, timeout_seconds=60)
    page.wait_for_timeout(_RESULT_SETTLE_MS)
    if _has_login_form(page.context.pages):
        raise AdapterLoginRequired("the central OA session is not logged in or has expired")
    if not _click_exact_visible_text(page.context.pages, _ALL_APPS_LABEL):
        raise SeeyonDocumentContractMismatch("OA All Apps entry was not found")
    page.wait_for_timeout(_RESULT_SETTLE_MS)
    if not _search_all_apps(page.context.pages, _DOCUMENT_CENTER_LABEL):
        raise SeeyonDocumentContractMismatch("OA app search field was not found")
    page.wait_for_timeout(_RESULT_SETTLE_MS)
    for label in (
        _DOCUMENT_CENTER_LABEL,
        _UNIT_DOCUMENTS_LABEL,
        _INTELLECTUAL_PROPERTY_LABEL,
        _GROUP_CERTIFICATES_LABEL,
        category_label,
    ):
        if not _click_exact_visible_text(page.context.pages, label):
            raise SeeyonDocumentContractMismatch(
                f"OA document center entry is unavailable: {label}"
            )
        page.wait_for_timeout(_RESULT_SETTLE_MS)
    frame = _find_document_frame(page.context.pages)
    if frame is None:
        raise SeeyonDocumentContractMismatch(
            "OA certificate folder search controls were not found"
        )
    return frame


def _search_current_folder(worker, *, frame, query: str) -> list[dict]:
    field = frame.locator("#frName")
    button = frame.locator("a.syIcon.sy-search.seary-bar-btn")
    if not field.count() or not button.count():
        raise SeeyonDocumentContractMismatch(
            "OA certificate folder search controls changed"
        )
    field.first.fill(query, timeout=5000)
    button.first.click(timeout=5000)
    worker.page.wait_for_timeout(_RESULT_SETTLE_MS)
    rows = frame.evaluate(
        """
        () => {
          const truthy = (value) => value === true || value === 'true' || value === 1;
          return Object.values(window.nowPageDr || {})
            .filter((row) => row && row.docResource && !row.docResource.isFolder)
            .map((row) => ({
              resource_id: String(row.docResource.id || row.id || ''),
              source_id: String(row.docResource.sourceId || ''),
              filename: String(row.docResource.frName || row.frName || '').trim(),
              display_size: String(row.frSize || row.docResource.frSize || '').trim(),
              create_date: String(row.createDate || row.docResource.createTime || ''),
              version: String(row.vForDocDownload || row.docResource.vForDocDownload || ''),
              mime_type_id: String(row.docResource.mimeTypeId || row.mimeTypeId || ''),
              secret_level: String(row.docResource.secretLevel || ''),
              read_acl: truthy(row.readAcl),
              download_acl: truthy(row.downloadAcl),
              is_upload_file: truthy(row.isUploadFile),
            }));
        }
        """
    )
    if not isinstance(rows, list):
        raise SeeyonDocumentContractMismatch(
            "OA certificate folder returned an invalid result set"
        )
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("filename") or "").lower().endswith(".pdf")
        and str(row.get("resource_id") or "")
        and str(row.get("source_id") or "")
    ]


def _find_document_frame(pages):
    for page in pages:
        for frame in page.frames:
            field = frame.locator("#frName")
            if field.count() and field.first.is_visible():
                return frame
    return None


def _search_all_apps(pages, value: str) -> bool:
    for page in pages:
        for frame in page.frames:
            locator = frame.get_by_placeholder(_APP_SEARCH_PLACEHOLDER, exact=True)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.fill("", timeout=5000)
                candidate.press_sequentially(value, delay=35, timeout=10000)
                clicked = candidate.evaluate(
                    """
                    (input) => {
                      const icon = input.parentElement
                        && input.parentElement.querySelector('i.vportal.vp-search-large');
                      if (!icon) return false;
                      icon.click();
                      return true;
                    }
                    """
                )
                return bool(clicked)
    return False


def _click_exact_visible_text(pages, label: str) -> bool:
    selectors = (
        "a,button,[role='button'],[onclick]",
        ".menu-expand-font,.menu-list-li-title,.quick-search-menu-item,.menu_btn.text_overflow",
    )
    for page in pages:
        for frame in page.frames:
            for selector_index, selector in enumerate(selectors):
                locator = frame.locator(selector)
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    text = " ".join((candidate.inner_text() or "").split())
                    if text != label or not candidate.is_visible():
                        continue
                    if selector_index == 0:
                        candidate.click(timeout=5000)
                    else:
                        candidate.evaluate("(element) => element.click()")
                    return True
    return False


def _has_login_form(pages) -> bool:
    for page in pages:
        for frame in page.frames:
            if frame.locator("input[type='password']").count():
                return True
    return False


def _public_document_item(row: dict, document_type: str, query: str) -> dict:
    title = _certificate_title(row["filename"])
    normalized_query = _normalize_text(query)
    normalized_title = _normalize_text(title)
    return {
        "title": title,
        "filename": row["filename"],
        "document_type": document_type,
        "display_size": row["display_size"],
        "match_kind": (
            "exact"
            if normalized_query == normalized_title
            else "contains"
        ),
    }


def _certificate_title(filename: str) -> str:
    value = re.sub(r"(?i)\.pdf$", "", str(filename or "")).strip()
    value = re.sub(r"^(?:\s*\u3010[^\u3011]+\u3011)+\s*", "", value)
    return value.strip()


def _row_matches_query(row: dict, query: str) -> bool:
    needle = _normalize_text(query)
    return needle in _normalize_text(row["filename"]) or needle in _normalize_text(
        _certificate_title(row["filename"])
    )


def _validated_query(value) -> str:
    query = " ".join(str(value or "").split())
    if len(query) < 2:
        raise ValueError("certificate name must contain at least 2 characters")
    if len(query) > 160:
        raise ValueError("certificate name is too long")
    return query


def _validated_queries(arguments: dict) -> list[str]:
    values = []
    if arguments.get("name") is not None:
        values.append(arguments.get("name"))
    supplied_names = arguments.get("names")
    if supplied_names is not None:
        if not isinstance(supplied_names, list):
            raise ValueError("certificate names must be an array")
        if len(supplied_names) < 1 or len(supplied_names) > 10:
            raise ValueError("certificate names must contain between 1 and 10 items")
        values.extend(supplied_names)
    if not values:
        raise ValueError("certificate name or names is required")
    queries = []
    seen = set()
    for value in values:
        query = _validated_query(value)
        key = _normalize_text(query)
        if key not in seen:
            seen.add(key)
            queries.append(query)
    return queries


def _validated_limit(value) -> int:
    if value is None:
        return 10
    if isinstance(value, bool):
        raise ValueError("limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    return limit


def _validated_reference(value) -> dict:
    if not isinstance(value, dict):
        raise SeeyonDocumentAccessDenied("document download reference is invalid")
    required = {
        "resource_id",
        "source_id",
        "filename",
        "document_type",
        "category_label",
        "create_date",
        "version",
        "mime_type_id",
    }
    if any(not str(value.get(name) or "").strip() for name in required):
        raise SeeyonDocumentAccessDenied("document download reference is incomplete")
    document_type = str(value["document_type"])
    if (
        document_type not in _CERTIFICATE_CATEGORIES
        or value["category_label"] != _CERTIFICATE_CATEGORIES[document_type]
    ):
        raise SeeyonDocumentAccessDenied("document download category binding is invalid")
    if not str(value["filename"]).lower().endswith(".pdf"):
        raise SeeyonDocumentAccessDenied("document download is not a PDF certificate")
    return dict(value)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", normalized)


def _match_rank(value: str) -> int:
    return {"exact": 0, "contains": 1}.get(value, 9)
