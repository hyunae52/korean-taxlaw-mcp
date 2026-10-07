"""사이트가 공용 액션을 쓰지 않는 문서구분.

해석례(01~04)와 결정례(05~10)는 검색 ``ASIPDI002PR01`` · 상세 ``ASIQTB002PR01`` 두
액션을 공유한다(:mod:`~korean_taxlaw_mcp.domains.documents`). 아래 세 문서구분은 그
경로를 타지 않는다.

* ``11`` 감사원 심사청구 — 전용 검색 액션만 있고 **HTML 본문이 없다**. 본문은 첨부 PDF/HWP
  이며, 원본 스토리지에 파일이 없으면 서버가 자기 404 페이지를 ``Content-Disposition``
  과 함께 돌려준다(:data:`ATTACHMENT_STORAGE_GAP_NOTE`).
* ``13`` 자주찾는 쟁점별 사례 — 큐레이션된 해석례. 전용 검색 액션과 쟁점 분류가 있고,
  상세는 공용 상세 액션(``ASIQTB002PR01``)을 그대로 쓴다.
* ``14`` 납세자보호위원회 심의사례 — 전용 검색 액션, 상세는 공용 상세 액션.

셋을 한 모듈로 묶은 이유: 셋 다 "공용 검색 경로를 쓰지 않는 예외"라 성격이 같고, 각각을
별 모듈로 나누면 세 파일이 서로 다른 관용구로 갈라진다. 대신 액션 ID·응답 필드 이름을
표로 고정해 두고 한 구현으로 처리한다.

검색 파라미터 의미는 전부 실측으로 확정했다(2026-09-28 기준).

* ``11`` — ``ntstDcmTtl`` 은 제목+요지 색인(전체 3,124건 중 '법인세' → 443건),
  ``ntstDcmDscmCntn`` 은 결정번호 정확일치('2025심사2038' → 1건). ``bltnStrtDt``/
  ``bltnEndDt`` 를 **빼면 서버가 status=ERROR 로 응답한다** — 빈 문자열이라도 반드시 넣는다.
* ``13`` — ``schNtstDcmTtl``·``schNtstDcmGistCntn``·``schNtstDcmDscmCntn`` 은 서로 AND 다
  ('상속' → 제목 126건 / 요지 163건 / 둘 다 117건). ``ntstDcmPntClCd``(쟁점분류 19396)
  필터가 그대로 먹는다('304001' → 20건).
* ``14`` — ``searchCondition`` + ``searchKeyword`` 조합이고 ``ntstDcmClCd="14"`` 를
  **강제로 넣어야 한다**(빼면 0건). 등록기간 필터는 ``bltnStrtDt``/``bltnEndDt`` 다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

import httpx

from ..action_client import call_action, get_client
from ..cache import TTL
from ..codes import DOC_CLASS_MENU
from ..config import NTS_ORIGIN
from ..errors import ErrorCode, NtsError, not_found, upstream
from ..model import AuthorityLevel, authority_for_doc_class
from ..payload import drop_empty
from ..query import format_date, to_site_date
from ..rate_limit import upstream_limiter

AUDIT_APPEAL = "11"
CURATED_ISSUE = "13"
TAXPAYER_PROTECTION = "14"

#: 공용 검색 경로(``ASIPDI002PR01``)를 쓰지 않는 문서구분.
SPECIAL_CLASSES: tuple[str, ...] = (AUDIT_APPEAL, CURATED_ISSUE, TAXPAYER_PROTECTION)

#: 문서구분 → 전용 검색 액션.
_SEARCH_ACTION: dict[str, str] = {
    AUDIT_APPEAL: "ASIPDM001MR01",
    CURATED_ISSUE: "ASIQTH001MR01",
    TAXPAYER_PROTECTION: "ASIPRC019MR02",
}

#: 응답에서 목록이 담기는 필드 이름. 액션마다 다르다 — 이름을 틀리면 조용히 0건이 된다.
_ROW_LIST_KEY: dict[str, str] = {
    AUDIT_APPEAL: "badiRvwDVOList",
    CURATED_ISSUE: "pntThanBkmrDVOList",
    TAXPAYER_PROTECTION: "dcmBscDVOList",
}

#: 액션 호출에 쓸 Referer(사이트 화면). 없어도 동작하지만 예의상 화면 주소를 보낸다.
SOURCE_PAGE: dict[str, str] = {
    AUDIT_APPEAL: f"{NTS_ORIGIN}/pd/USEPDM001M.do",
    CURATED_ISSUE: f"{NTS_ORIGIN}/qt/USEQTH001M.do",
    TAXPAYER_PROTECTION: f"{NTS_ORIGIN}/bg/USEBGF001M.do",
}

#: 첨부 파일 메타데이터 액션. fleId(+fleSn)를 주면 파일명·형식·크기·스토리지 경로·
#: 다운로드 URI 를 돌려준다.
FILE_INFO_ACTION = "ACMCMA001MR02"

#: 첨부 다운로드 경로. 목록 행의 fleDwldUri 가 가리키는 곳과 같다.
ATTACHMENT_PATH = "/downloadFile.do"

#: 11 의 결정번호·제목·요지 화면(행별 딥링크가 없다 — 팝업이 목록 전체를 다시 그린다).
AUDIT_APPEAL_LIST_URL = SOURCE_PAGE[AUDIT_APPEAL]

#: Range를 무시하는 서버에서도 이만큼만 읽고 스트림을 닫는다.
_ATTACHMENT_PREFIX_BYTES = 4096

ATTACHMENT_STORAGE_GAP_NOTE = (
    "2026-09-28 조사 표본에서 메타데이터가 존재해도 파일 대신 오류 HTML이 반환됐습니다. "
    "이 기록은 과거 관측이며 현재 확보 여부는 각 attachment 상태를 확인하세요. "
    "본문 미확보를 문서 부존재나 물리 스토리지 상태의 확정으로 해석하지 마세요."
)

#: 실측 조건 — 문서 부존재로 오해하지 않도록 조사 시점과 경계를 함께 남긴다.
ATTACHMENT_STORAGE_GAP_DETECTED_ON = "2026-09-28"
ATTACHMENT_STORAGE_GAP_FIRST_BAD_BATCH = "2026-02-06"

#: 스토리지 적재 배치(flePth)별 다운로드 성공/시도 실측치. 11 문서구분 한정.
ATTACHMENT_STORAGE_GAP_MEASURED: list[dict[str, Any]] = [
    {"storageBatch": "blrd/20251113", "checked": 56, "downloaded": 56},
    {"storageBatch": "blrd/20260108", "checked": 33, "downloaded": 33},
    {"storageBatch": "blrd/20260206", "checked": 12, "downloaded": 0},
    {"storageBatch": "blrd/20260611", "checked": 16, "downloaded": 0},
    {"storageBatch": "blrd/20260730", "checked": 3, "downloaded": 0},
]

_DOC_TYPE_LABEL: dict[str, str] = {
    AUDIT_APPEAL: "감사원 심사청구",
    CURATED_ISSUE: "자주찾는 쟁점별 사례",
    TAXPAYER_PROTECTION: "납세자보호위원회 심의사례",
}

#: 감사원 심사청구의 결정번호 모양('2025심사2038'·'2011감심200').
_DECISION_NUMBER = re.compile(r"^\d{4}\s*(감심|심사|심판|적부|이의)\s*-?\s*\d{1,6}$")


def _label(doc_class: str) -> str:
    """화면 표기 이름. 없으면 코드표(DOC_CLASS) 값으로 되돌아간다."""
    return DOC_CLASS_MENU.get(doc_class) or _DOC_TYPE_LABEL.get(doc_class) or doc_class


def _domain_for(doc_class: str) -> str:
    """13 은 큐레이션된 해석례라 interpretation, 11·14 는 불복·심의 결정이다."""
    return "interpretation" if doc_class == CURATED_ISSUE else "decision"


def attachment_url(fle_id: str, fle_sn: str | int) -> str:
    """첨부 다운로드 주소. 목록 행의 fleId·fleSn 만으로 만들 수 있다."""
    return f"{NTS_ORIGIN}{ATTACHMENT_PATH}?{urlencode({'fleId': fle_id, 'fleSn': fle_sn})}"


def _params(
    doc_class: str,
    *,
    query: str | None,
    tax_type_codes: list[str] | None,
    issue_codes: list[str] | None,
    date_from: str | None,
    date_to: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    """문서구분별 검색 파라미터. 필드 이름이 셋 다 다르므로 분기한다."""
    start_date = to_site_date(date_from)
    end_date = to_site_date(date_to)
    for name, value, converted in (
        ("date_from", date_from, start_date), ("date_to", date_to, end_date),
    ):
        if value and not converted:
            raise NtsError(
                ErrorCode.INVALID_INPUT, f"{name} 형식이 올바르지 않습니다: {value}",
                hints=["YYYY, YYYY-MM, YYYY-MM-DD 또는 구분자 없는 숫자 형식을 사용하세요."],
            )
    text = (query or "").strip()

    if doc_class == AUDIT_APPEAL:
        # bltnStrtDt/bltnEndDt 를 빼면 서버가 ERROR 를 돌려준다(실측) — 빈 값도 넣는다.
        return {
            "ntstDcmDscmCntn": text if _looks_like_decision_number(text) else "",
            "ntstDcmTtl": "" if _looks_like_decision_number(text) else text,
            "bltnStrtDt": start_date,
            "bltnEndDt": end_date,
            "lnkClCd": "02",
            "pageIndex": page,
            "recordCountPerPage": limit,
        }

    if doc_class == CURATED_ISSUE:
        return {
            "ntstDcmClCd": "",
            "ntstTlawClCd": (tax_type_codes or [""])[0],
            "ntstDcmPntClCd": (issue_codes or [""])[0],
            "schNtstDcmTtl": "",
            "schNtstDcmGistCntn": text,
            "schNtstDcmDscmCntn": "",
            "pageIndex": page,
            "recordCountPerPage": limit,
        }

    # TAXPAYER_PROTECTION — ntstDcmClCd 를 빼면 0건이 온다(실측).
    return {
        "searchCondition": "ntstDcmTtl" if text else "",
        "searchKeyword": text,
        "bltnStrtDt": start_date,
        "bltnEndDt": end_date,
        "pageIndex": page,
        "recordCountPerPage": limit,
        "ntstDcmClCd": TAXPAYER_PROTECTION,
    }


def _looks_like_decision_number(text: str) -> bool:
    """'2025심사2038'·'2011감심200' 같은 결정번호 모양인지.

    감사원 심사청구 검색창은 결정번호와 제목·요지가 **따로** 있고 서로 다른 필드다.
    한 낱말을 양쪽에 넣으면 두 조건이 모두 걸려 0건이 되므로 한쪽만 고른다.
    """
    if not text or len(text) > 24 or " " in text:
        return False
    return bool(_DECISION_NUMBER.match(text))


async def search_special_documents(
    *,
    doc_class: str,
    query: str | None = None,
    tax_type_codes: list[str] | None = None,
    issue_codes: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    limit: int = 20,
    validate_raw_rows: bool = False,
) -> dict[str, Any]:
    """11·13·14 목록 검색. 반환 형태는 ``search_documents`` 와 맞춘다.

    세 액션 모두 ``startCount`` 오프셋이 아니라 1부터 시작하는 ``pageIndex`` 를 쓴다
    (국세법령정보시스템 공통 규칙 — 실측).
    """
    if doc_class not in SPECIAL_CLASSES:
        raise NtsError(
            ErrorCode.INVALID_INPUT,
            f"special 검색 대상이 아닌 문서구분입니다: {doc_class}",
            hints=[f"지원 문서구분: {', '.join(SPECIAL_CLASSES)}"],
        )

    page = max(1, page)
    limit = min(100, max(1, limit))
    action_id = _SEARCH_ACTION[doc_class]

    payload = await call_action(
        action_id,
        _params(
            doc_class,
            query=query,
            tax_type_codes=tax_type_codes,
            issue_codes=issue_codes,
            date_from=date_from,
            date_to=date_to,
            page=page,
            limit=limit,
        ),
        referer=SOURCE_PAGE[doc_class],
        ttl=TTL.SEARCH,
    )

    key = _ROW_LIST_KEY[doc_class]
    if not isinstance(payload, dict) or key not in payload or "recordCount" not in payload:
        raise upstream("검색 응답의 목록 또는 건수 필드가 없습니다.", actionId=action_id)
    rows = payload[key]
    try:
        total = int(payload["recordCount"])
    except (ValueError, TypeError) as exc:
        raise upstream("검색 응답의 건수 형식이 잘못됐습니다.", actionId=action_id) from exc
    if rows is None and total == 0:
        rows = []
    if total < 0 or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise upstream("검색 응답의 목록 형식이 잘못됐습니다.", actionId=action_id)
    if not rows and total > (page - 1) * limit:
        raise upstream("검색 건수와 빈 목록이 일치하지 않습니다.", actionId=action_id, page=page)
    # Audit exact lookup relies on these rows as identity evidence. Never hide
    # contradictory or overfull raw rows behind the presentation slice below.
    if validate_raw_rows and (len(rows) > limit or len(rows) > max(0, total - (page - 1) * limit)):
        raise upstream("감사원 검색 원본 행 수가 건수 또는 페이지 상한과 일치하지 않습니다.",
                       actionId=action_id, page=page)
    level = str(authority_for_doc_class(doc_class))
    items = [_row_to_item(doc_class, row, level) for row in rows[:limit]]

    out: dict[str, Any] = {
        "docClass": doc_class,
        "documentType": _label(doc_class),
        "authorityLevel": level,
        "sourcePage": SOURCE_PAGE[doc_class],
        "total": total,
        "page": page,
        "limit": limit,
        "items": items,
    }
    if doc_class == AUDIT_APPEAL:
        # 11 은 상세 액션이 없고 행별 딥링크도 없다. 본문 확보 경로를 명시한다.
        out["bodyUnavailable"] = True
        out["bodyNote"] = (
            "감사원 심사청구는 사이트가 본문을 HTML 로 제공하지 않습니다. 본문은 행의 "
            "attachment(첨부 PDF/HWP)에만 있고, 일부 조사 표본은 파일 대신 오류 HTML을 반환했습니다. "
            "attachment_status=true 로 행별 확보 여부를 확인할 수 있습니다. PDF 본문은 "
            "get_tax_document(document_number=결정번호)로 읽습니다. HWP와 OCR은 지원하지 않습니다."
        )
    elif doc_class == TAXPAYER_PROTECTION:
        out["note"] = "본문은 get_tax_document(ntst_dcm_id)로 조회합니다."
    else:
        out["note"] = (
            "13 은 사전답변·질의회신을 쟁점별로 다시 묶은 목록입니다. 본문은 "
            "get_tax_document(ntst_dcm_id)로 조회하며, 상세의 문서구분은 원래 구분(01·02 등)입니다."
        )
    return out


def _row_to_item(doc_class: str, row: dict[str, Any], level: str) -> dict[str, Any]:
    """검색 행 → 요약 항목. 세 액션의 필드 이름이 달라 분기한다."""
    if doc_class == AUDIT_APPEAL:
        fle_id = str(row.get("fleId") or "").strip()
        fle_sn = row.get("fleSn")
        item: dict[str, Any] = {
            "documentType": _label(doc_class),
            "documentNumber": str(row.get("ntstDcmDscmCntn") or "").strip() or None,
            "title": str(row.get("ntstDcmTtl") or "").strip() or None,
            "taxType": str(row.get("ntstTlawNm") or "").strip() or None,
            "decisionResult": str(row.get("ntstDcmDcsNm") or "").strip() or None,
            # 사이트 목록의 이 열은 결정일이다(rgtDt). 다른 검색 도구와 키를 맞추려고
            # registrationDate 를 쓰되, 의미가 다르므로 주석으로 남긴다.
            "registrationDate": format_date(row.get("rgtDt")),
            "summary": str(row.get("ntstDcmGistCntn") or "").strip() or None,
            "authorityLevel": level,
            "attachment": drop_empty(
                {
                    "fleId": fle_id or None,
                    "fleSn": fle_sn,
                    "downloadUrl": attachment_url(fle_id, fle_sn) if fle_id and fle_sn is not None else None,
                }
            ),
        }
        return drop_empty(item)

    if doc_class == CURATED_ISSUE:
        return drop_empty(
            {
                "documentType": _label(doc_class),
                "documentNumber": str(row.get("ntstDcmDscmCntn") or "").strip() or None,
                "title": str(row.get("ntstDcmTtl") or "").strip() or None,
                "taxType": str(row.get("ntstTlawClNm") or "").strip() or None,
                "summary": str(row.get("ntstDcmGistCntn") or "").strip() or None,
                # 쟁점 분류는 코드표(19396)를 상수로 고정하지 않고 원본 이름을 그대로 쓴다 —
                # 표를 검증하지 못한 상태에서 코드를 지어내면 없는 분류를 만들어낸다.
                "issueCategory": str(row.get("ntstDcmPntClNm") or "").strip() or None,
                "issueCategoryCode": str(row.get("ntstDcmPntClCd") or "").strip() or None,
                "originalDocClass": str(row.get("ntstDcmClNm") or "").strip() or None,
                "authorityLevel": level,
                "ntstDcmId": str(row.get("ntstDcmId") or "").strip() or None,
            }
        )

    return drop_empty(
        {
            "documentType": _label(doc_class),
            "title": str(row.get("ntstDcmTtl") or "").strip() or None,
            "taxType": str(row.get("ntstTLawClNm") or "").strip() or None,
            "registrationDate": format_date(row.get("frsRgtDtm") or row.get("ntstDcmRgtDt")),
            "author": str(row.get("fnm") or "").strip() or None,
            "summary": str(row.get("ntstDcmGistCntn") or "").strip() or None,
            "documentNumber": str(row.get("ntstDcmDscmCntn") or "").strip() or None,
            "authorityLevel": level,
            "ntstDcmId": str(row.get("ntstDcmId") or "").strip() or None,
        }
    )


async def get_attachment_info(
    fle_id: str, fle_sn: str, *, verify: bool = True
) -> dict[str, Any]:
    """첨부 1건의 메타데이터와 (선택) 실제 확보 가능 여부.

    ``verify=True`` 이면 다운로드를 **가볍게** 확인한다. 본문을 저장하지 않고 판정만
    하며, 실패하면 그대로 이유를 남긴다 — 서버 오류 페이지를 파일로 저장하지 않는다.
    """
    fid = str(fle_id or "").strip()
    fsn = str(fle_sn).strip() if fle_sn is not None else ""
    if not fid or not fsn:
        raise NtsError(ErrorCode.INVALID_INPUT, "fle_id 와 fle_sn 이 모두 필요합니다.")

    referer = SOURCE_PAGE[AUDIT_APPEAL]
    raw = await call_action(
        FILE_INFO_ACTION, {"fleId": fid, "fleSn": fsn}, referer=referer, ttl=TTL.STATIC
    )
    row = raw[0] if isinstance(raw, list) and raw else None
    if not row:
        raise not_found(
            f"fleId {fid} / fleSn {fsn} 에 해당하는 첨부 파일 정보를 찾지 못했습니다.",
            ["목록 행의 attachment.fleId·fleSn 값을 그대로 쓰세요."],
        )

    base = str(row.get("orcFleNm") or "").strip()
    extension = str(row.get("fleXsnNm") or "").strip()
    url = attachment_url(fid, fsn)
    info: dict[str, Any] = {
        "fileName": f"{base}.{extension}" if base and extension else (base or None),
        "format": extension or None,
        "sizeBytes": row.get("fleSz") or None,
        "storageBatch": row.get("flePth") or None,
        "downloadUrl": url,
    }

    if verify:
        available, reason = await probe_attachment(url, referer=referer)
        info["available"] = available
        info["availabilityCheck"] = "file_signature_prefix_only"
        if not available:
            info["unavailableReason"] = reason or ATTACHMENT_STORAGE_GAP_NOTE
    return drop_empty(info)


async def probe_attachment(url: str, *, referer: str | None = None) -> tuple[bool, str | None]:
    """첨부가 실제로 내려오는지 확인한다. ``(확보가능, 불가사유)``.

    헤더만으로 성공을 선언하지 않는다. Range GET으로 최대 4 KiB의 파일 서명을
    확인하며, Range가 무시돼도 본문 전체를 내려받지 않는다. 전체 파일 무결성이나
    텍스트 추출의 성공을 뜻하지 않는다. 장애/알 수 없는 응답은 부존재와 구분한다.
    """
    verdict = upstream_limiter.take(1)
    if not verdict.ok:
        raise NtsError(
            ErrorCode.RATE_LIMITED,
            f"요청 한도 초과: {verdict.retry_after_sec}초 후 재시도하세요.",
            detail={"retryAfterSec": verdict.retry_after_sec},
        )

    client = await get_client()
    headers = {"referer": referer or SOURCE_PAGE[AUDIT_APPEAL],
               "range": f"bytes=0-{_ATTACHMENT_PREFIX_BYTES - 1}", "accept-encoding": "identity"}
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code in (404, 410):
                return False, f"첨부 URL이 HTTP {response.status_code}로 응답했습니다(원문 파일 미제공)."
            if response.status_code not in (200, 206):
                raise upstream("첨부 확인 응답을 검증하지 못했습니다.",
                               status=response.status_code)
            prefix = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=_ATTACHMENT_PREFIX_BYTES):
                prefix.extend(chunk[:_ATTACHMENT_PREFIX_BYTES - len(prefix)])
                if len(prefix) >= _ATTACHMENT_PREFIX_BYTES:
                    break
    except httpx.HTTPError as exc:
        raise upstream(f"첨부 확인 요청 실패: {exc}", url=url) from exc
    body = bytes(prefix)
    if _is_error_page(body):
        return False, "첨부 URL이 파일 대신 HTML을 반환했습니다(원문 파일 미제공). 저장소 공백인지 일시 장애인지는 별도 확인이 필요합니다."
    if body.startswith((b"%PDF-", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"HWP Document File", b"PK\x03\x04")):
        return True, None
    raise upstream("첨부 응답에서 지원 파일 서명을 확인하지 못했습니다. 파일 부존재로 판단하지 않습니다.")


def _is_error_page(body: bytes) -> bool:
    """오류 페이지 판정.

    서버는 Content-Disposition 을 정상으로 붙이면서 본문만 자기 404 HTML 로 바꾼다.
    그래서 확장자가 아니라 **본문 선두**를 본다(대소문자·선행 개행 무시).
    """
    head = body[:64].lstrip()
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:].lstrip()
    head = head.lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


async def attachment_status(fle_id: str, fle_sn: str) -> dict[str, Any]:
    """검색 행에 붙일 첨부 확보 상태.

    선택적 부가 정보라서 실패해도 검색 자체는 살린다. 다만 오류를 조용히 삼키지 않고
    ``statusError`` 로 그대로 싣는다 — 조회 실패를 "파일 없음"으로 바꾸지 않는다.
    """
    try:
        info = await get_attachment_info(fle_id, fle_sn, verify=True)
    except NtsError as exc:
        return {"available": None, "statusError": str(exc.code), "statusMessage": exc.message}
    except Exception as exc:  # noqa: BLE001 — 부가 정보 실패가 검색을 죽이면 안 된다
        return {"available": None, "statusError": "UPSTREAM_ERROR", "statusMessage": str(exc)}
    return info
