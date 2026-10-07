"""Bounded PDF text-layer extraction; no OCR, summarization, or legal inference.

Production calls this module in a short-lived subprocess so parser timeouts and
cancellation do not leave work running in the MCP event loop.
"""
from __future__ import annotations

from io import BytesIO
import json
import sys
from typing import Any

from .errors import ErrorCode, NtsError

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_PAGE_WINDOW = 20
MAX_BODY_CHARS = 200_000
WORKER_TIMEOUT = 15


def extract_pdf(data: bytes, page_start: int, page_end: int | None, body_limit: int) -> dict[str, Any]:
    """Offsets address Unicode code points in fullText; page numbers are physical PDF pages."""
    from pypdf import PdfReader

    if len(data) > MAX_PDF_BYTES:
        raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "PDF 크기 상한을 초과했습니다.",
                       detail={"reason": "PDF_SIZE_LIMIT"})
    if not 1 <= page_start <= MAX_PDF_PAGES or not 500 <= body_limit <= MAX_BODY_CHARS:
        raise NtsError(ErrorCode.INVALID_INPUT, "PDF 페이지 또는 본문 크기 범위가 잘못됐습니다.")
    if page_end is not None and not page_start <= page_end < page_start + MAX_PAGE_WINDOW:
        raise NtsError(ErrorCode.INVALID_INPUT, "PDF는 한 번에 최대 20페이지를 조회합니다.")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
        raise NtsError(ErrorCode.PARSE_ERROR, "완전한 PDF 파일을 확인하지 못했습니다.",
                       detail={"reason": "INVALID_PDF"})
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "암호화된 PDF는 본문을 추출하지 않습니다.",
                       detail={"reason": "ENCRYPTED_PDF"})
    count = len(reader.pages)
    if count > MAX_PDF_PAGES:
        raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "PDF 페이지 수 상한을 초과했습니다.",
                       detail={"reason": "PDF_PAGE_LIMIT", "pageCount": count})
    if not count or page_start > count or (page_end is not None and page_end > count):
        raise NtsError(ErrorCode.INVALID_INPUT, "요청한 페이지가 PDF 범위를 벗어났습니다.",
                       detail={"pageCount": count})
    end = page_end if page_end is not None else min(count, page_start + MAX_PAGE_WINDOW - 1)
    chunks: list[str] = []
    pages: list[dict[str, Any]] = []
    length = 0
    next_page = None
    cut = False
    for number in range(page_start, end + 1):
        # A single parser failure invalidates this extraction, not just that page.
        text = reader.pages[number - 1].extract_text() or ""
        # Blank pages retain provenance but must not consume the text budget or
        # prevent the first readable page from being returned in part.
        if not text.strip():
            text = ""
        separator = "\n\n" if chunks else ""
        available = max(0, body_limit - length - (len(separator) if text else 0))
        if text and len(text) > available and chunks:
            next_page, cut = number, True
            break
        truncated = len(text) > available
        value = text[:max(0, available)]
        start = length + (len(separator) if value else 0)
        if value:
            chunks.append(separator + value)
            length = start + len(value)
        pages.append({"pageNumber": number, "start": start, "end": start + len(value),
                      "textStatus": "extracted" if value.strip() else "no_extractable_text",
                      "truncated": truncated})
        if truncated:
            next_page, cut = number, True
            break
    if next_page is None and end < count:
        next_page = end + 1
    body = "".join(chunks)
    unread = [p["pageNumber"] for p in pages if p["textStatus"] == "no_extractable_text"]
    return {"fullText": body, "pages": pages, "pageCount": count,
            "pageStart": page_start, "pageEnd": pages[-1]["pageNumber"],
            "nextPage": next_page, "bodyPartial": page_start != 1 or end != count or cut or bool(unread),
            "fullTextTruncated": cut, "pagesWithoutText": unread,
            "extractionMethod": "pdf_text_layer", "offsetUnit": "unicode_codepoint",
            "completeness": "unverified",
            "extractionNote": "텍스트 레이어만 추출했습니다. 표의 읽는 순서·이미지 안의 글자·문자 인코딩은 원문 대조가 필요합니다."
            + (" 한 페이지 중간에서 잘렸다면 해당 페이지만 더 큰 body_limit로 다시 조회하세요." if cut else "")}


def _main() -> None:
    # Linux production receives hard CPU/address-space limits as well as the
    # parent's wall timeout. Windows still isolates and terminates the worker.
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    try:
        data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        result = extract_pdf(data, int(sys.argv[1]), int(sys.argv[2]) or None, int(sys.argv[3]))
        result = {"ok": True, "result": result}
    except NtsError as exc:
        result = exc.envelope()
    except Exception:
        result = NtsError(ErrorCode.PARSE_ERROR, "PDF 본문 추출에 실패했습니다. 원문 링크에서 확인하세요.",
                          detail={"reason": "PDF_PARSE_FAILED"}).envelope()
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    _main()
