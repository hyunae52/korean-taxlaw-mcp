"""Read the official attachment of an exactly identified Board of Audit decision."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import sys
from typing import Any

import httpx

from ..action_client import get_client
from ..config import NTS
from ..errors import ErrorCode, NtsError, not_found, upstream
from ..pdf_text import MAX_BODY_CHARS, MAX_PAGE_WINDOW, MAX_PDF_BYTES, MAX_PDF_PAGES, WORKER_TIMEOUT
from ..rate_limit import upstream_limiter
from .special import AUDIT_APPEAL, AUDIT_APPEAL_LIST_URL, attachment_url, search_special_documents

_NUMBER = re.compile(r"([0-9]{4})\s*-?\s*(심사|감심)\s*-?\s*([0-9]{1,6})")
_slots = asyncio.Semaphore(2)


def audit_number(value: str | None) -> str | None:
    match = _NUMBER.fullmatch((value or "").strip())
    return f"{match[1]}{match[2]}{int(match[3])}" if match else None


async def _download(url: str) -> bytes:
    verdict = upstream_limiter.take(1)
    if not verdict.ok:
        raise NtsError(ErrorCode.RATE_LIMITED, "첨부 다운로드 요청 한도에 도달했습니다.",
                       detail={"retryAfterSec": verdict.retry_after_sec})
    client = await get_client()
    try:
        async with asyncio.timeout(min(NTS.timeout_seconds, 20)):
            async with client.stream("GET", url, follow_redirects=False, headers={
                "referer": AUDIT_APPEAL_LIST_URL, "accept-encoding": "identity",
            }) as response:
                if response.status_code in (404, 410):
                    raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "첨부파일을 제공받지 못했습니다.",
                                   detail={"reason": "ATTACHMENT_UNAVAILABLE"})
                if response.status_code != 200:
                    raise upstream("첨부 다운로드가 정상 응답을 반환하지 않았습니다.", status=response.status_code)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise upstream("압축 전송된 첨부 응답은 처리하지 않습니다.")
                size = response.headers.get("content-length")
                if size and (not size.isdecimal() or int(size) > MAX_PDF_BYTES):
                    raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "첨부 크기를 확인할 수 없거나 상한을 초과합니다.",
                                   detail={"reason": "PDF_SIZE_LIMIT"})
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                    if len(data) + len(chunk) > MAX_PDF_BYTES:
                        raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "PDF 크기 상한을 초과했습니다.",
                                       detail={"reason": "PDF_SIZE_LIMIT"})
                    data.extend(chunk)
                if size and len(data) != int(size):
                    raise upstream("첨부 응답 길이가 일치하지 않습니다.")
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise NtsError(ErrorCode.TIMEOUT, "첨부 다운로드 시간이 초과됐습니다.") from exc
    except httpx.HTTPError as exc:
        raise upstream("첨부 다운로드 통신 오류입니다.") from exc
    if not data.startswith(b"%PDF-"):
        if data.startswith((b"\xd0\xcf\x11\xe0", b"HWP Document File", b"PK\x03\x04")):
            raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "현재 첨부 본문 추출은 PDF만 지원합니다. HWP/HWPX는 원문을 확인하세요.",
                           detail={"reason": "UNSUPPORTED_ATTACHMENT_FORMAT"})
        raise upstream("PDF 대신 HTML·빈 응답 또는 알 수 없는 파일이 반환됐습니다.")
    return bytes(data)


async def _extract(data: bytes, page_start: int, page_end: int | None, body_limit: int) -> dict[str, Any]:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-m", "korean_taxlaw_mcp.pdf_text", str(page_start), str(page_end or 0), str(body_limit),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **options,
        )
    except OSError as exc:
        raise NtsError(ErrorCode.PARSE_ERROR, "PDF 처리 프로세스를 시작하지 못했습니다.") from exc
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(data), WORKER_TIMEOUT)
        if process.returncode:
            raise NtsError(ErrorCode.PARSE_ERROR, "PDF 처리 프로세스가 종료됐습니다. 본문은 확보하지 못했습니다.")
        payload = json.loads(stdout)
        if not payload.get("ok"):
            error = payload["error"]
            raise NtsError(ErrorCode(error["code"]), error["message"], detail=error.get("detail"))
        return payload["result"]
    except TimeoutError as exc:
        raise NtsError(ErrorCode.TIMEOUT, "PDF 본문 추출 시간이 초과됐습니다.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise NtsError(ErrorCode.PARSE_ERROR, "PDF 처리 결과를 확인하지 못했습니다.") from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def get_audit_document(document_number: str, *, include_full_text: bool = True,
                             body_limit: int | None = None, page_start: int = 1,
                             page_end: int | None = None) -> dict[str, Any]:
    number = audit_number(document_number)
    limit = min(NTS.body_limit, MAX_BODY_CHARS) if body_limit is None else body_limit
    if not number or not 500 <= limit <= MAX_BODY_CHARS or not 1 <= page_start <= MAX_PDF_PAGES:
        raise NtsError(ErrorCode.INVALID_INPUT, "감사원 결정번호 또는 PDF 조회 범위가 잘못됐습니다.")
    if page_end is not None and not page_start <= page_end <= min(MAX_PDF_PAGES, page_start + MAX_PAGE_WINDOW - 1):
        raise NtsError(ErrorCode.INVALID_INPUT, "PDF는 한 번에 최대 20페이지를 조회합니다.")
    if not include_full_text and (page_start != 1 or page_end is not None):
        raise NtsError(ErrorCode.INVALID_INPUT, "본문을 생략할 때 페이지 범위를 지정할 수 없습니다.")
    listing = await search_special_documents(doc_class=AUDIT_APPEAL, query=number, limit=100)
    if listing["total"] > len(listing["items"]):
        raise NtsError(ErrorCode.LOOKUP_INCOMPLETE, "감사원 결정번호 검색 결과를 끝까지 확인하지 못했습니다.")
    if listing["total"] < len(listing["items"]):
        raise upstream("감사원 검색 건수와 반환된 목록이 일치하지 않습니다.")
    matches = [item for item in listing["items"] if audit_number(item.get("documentNumber")) == number]
    if not matches:
        raise not_found("정확히 일치하는 감사원 결정번호를 찾지 못했습니다.")
    if len(matches) != 1:
        raise NtsError(ErrorCode.AMBIGUOUS_DOCUMENT_NUMBER, "동일한 감사원 결정번호가 여러 건입니다.",
                       detail={"candidates": matches})
    row = matches[0]
    attachment = row.get("attachment") or {}
    fid, serial = str(attachment.get("fleId", "")), str(attachment.get("fleSn", ""))
    valid_ids = bool(re.fullmatch(r"[0-9]{1,30}", fid) and re.fullmatch(r"[0-9]{1,20}", serial))
    # Construct the only permitted endpoint; never fetch a caller- or source-supplied URL.
    url = attachment_url(fid, serial) if valid_ids else AUDIT_APPEAL_LIST_URL
    document = {**row, "documentId": f"audit:{number}", "docClass": AUDIT_APPEAL,
                "decisionDate": row.get("registrationDate"), "sourceUrl": url,
                "sourcePage": AUDIT_APPEAL_LIST_URL, "bodyUnavailable": True}
    if not include_full_text:
        document["bodyNotRequested"] = True
        return document
    if not valid_ids:
        raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "감사원 결정의 첨부 식별자를 확인하지 못했습니다.",
                       detail={"document": document, "reason": "MISSING_ATTACHMENT_IDS"})
    if _slots.locked():
        raise NtsError(ErrorCode.RATE_LIMITED, "PDF 처리 중입니다. 잠시 후 다시 요청하세요.",
                       detail={"retryAfterSec": 1})
    async with _slots:
        try:
            data = await _download(url)
            extraction = await _extract(data, page_start, page_end, limit)
            document.update(extraction)
            document["attachment"] = {**attachment, "sha256": hashlib.sha256(data).hexdigest(), "downloadedBytes": len(data)}
            document["retrievedAt"] = datetime.now(timezone.utc).isoformat()
            for page in document["pages"]:
                page["sourceUrl"] = f"{url}#page={page['pageNumber']}"
            if not document["fullText"].strip():
                raise NtsError(ErrorCode.DETAIL_NOT_AVAILABLE, "선택한 PDF 페이지에서 읽을 수 있는 텍스트를 추출하지 못했습니다.",
                               detail={"reason": "NO_EXTRACTABLE_TEXT"},
                               hints=["스캔본·빈 페이지·미지원 문자 인코딩일 수 있습니다. 원문 또는 OCR로 확인하세요."])
            document["bodyUnavailable"] = False
            return document
        except NtsError as exc:
            raise NtsError(exc.code, exc.message, hints=exc.hints,
                           detail={**(exc.detail or {}), "document": document}) from exc
