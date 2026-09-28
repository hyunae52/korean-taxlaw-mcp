"""11·13·14 문서구분 회귀 테스트.

이 세 문서구분은 국세법령정보시스템이 공용 검색 액션(``ASIPDI002PR01``)을 쓰지 않는
예외다(+ :mod:`korean_taxlaw_mcp.domains.special`). 그래서 공용 경로 회귀 테스트
(:mod:`tests.test_tools`)와 분리해 두고, 업스트림은 fixture 로 대신한다.

``11`` 감사원 심사청구는 본문이 없고 첨부 PDF/HWP 로만 존재하므로, 첨부 다운로드
확인(최대 4 KiB Range GET)도 respx 로 함께 막는다 — 네트워크 없이 "본문 미제공" 판정까지
검증하려는 목적이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from korean_taxlaw_mcp.action_client import ACTION_URL, close_client
from korean_taxlaw_mcp.cache import cache
from korean_taxlaw_mcp.domains import special
from korean_taxlaw_mcp.routing import route_query
from korean_taxlaw_mcp.server import TOOL_NAMES, mcp

from .conftest import load, requires_fixtures

pytestmark = requires_fixtures

DOWNLOAD_URL = "https://taxlaw.nts.go.kr/downloadFile.do"

#: 실측: 파일이 없으면 서버가 이 크기의 자체 404 페이지를 Content-Disposition 과 함께 보낸다.
_ERROR_PAGE_BYTES = 1992


def _envelope(action_id: str, payload) -> dict:
    return {"status": "SUCCESS", "message": None, "data": {action_id: payload}}


class SpecialUpstream:
    """11·13·14 전용 액션 + 첨부 다운로드를 fixture 로 대신한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload: dict[str, dict] = {
            "ASIPDM001MR01": load("special_audit_appeal_search"),
            "ASIQTH001MR01": load("special_curated_issue_search"),
            "ASIPRC019MR02": load("special_taxpayer_protection_search"),
            "ACMCMA001MR02": load("special_fileinfo_missing"),
            "ASIQTB002PR01": load("special_taxpayer_protection_detail"),
        }
        #: True 면 첨부 파일이 실제로 내려오는 상태를 흉내낸다.
        self.attachment_available = False

    def last_params(self, action_id: str) -> dict:
        for recorded_id, params in reversed(self.calls):
            if recorded_id == action_id:
                return params
        raise AssertionError(f"{action_id} 호출 기록이 없습니다")

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        action_id = form["actionId"]
        self.calls.append((action_id, json.loads(form["paramData"])))
        return httpx.Response(200, json=_envelope(action_id, self.payload.get(action_id, {})))

    def _download_head(self) -> httpx.Response:
        length = "217415" if self.attachment_available else str(_ERROR_PAGE_BYTES)
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream", "content-length": length},
        )

    def _download_body(self) -> httpx.Response:
        body = b"%PDF-1.4\n" if self.attachment_available else b'\n<!DOCTYPE html>\n<html lang="ko"></html>'
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=body
        )


@pytest.fixture
async def upstream():
    cache.clear()
    up = SpecialUpstream()
    with respx.mock(assert_all_called=False) as mock:
        mock.post(ACTION_URL).mock(side_effect=up.handler)
        mock.head(url__startswith=DOWNLOAD_URL).mock(
            side_effect=lambda request: up._download_head()
        )
        mock.get(url__startswith=DOWNLOAD_URL).mock(
            side_effect=lambda request: up._download_body()
        )
        yield up
    cache.clear()
    await close_client()


async def call(name: str, args: dict) -> tuple[str, dict | None]:
    from fastmcp import Client

    async with Client(mcp) as client:
        result = await client.call_tool(name, args)
    text = result.content[0].text
    label = text.split("]")[0].lstrip("[")
    try:
        return label, json.loads(text[text.index("\n") + 1 :])
    except ValueError:
        return label, None


# ─── 도구 목록 계약 ────────────────────────────────────────────────────────────

async def test_no_new_tool_was_added() -> None:
    """11·13·14 는 기존 통합 도구의 type 필터로만 노출한다.

    자료 종류마다 도구를 만들면 모델의 선택 정확도가 떨어진다는 이 서버의 설계를
    그대로 지킨다 — 첨부 확인도 ``search_tax_decisions(attachment_status=true)`` 로
    표현한다. 도구 수 상한은 tests/test_tools.py 가 따로 지킨다.
    """
    assert len(TOOL_NAMES) == 12
    assert "get_tax_attachment" not in TOOL_NAMES


# ─── 11 감사원 심사청구 ────────────────────────────────────────────────────────

async def test_audit_appeal_always_sends_registration_date_params(upstream) -> None:
    """bltnStrtDt/bltnEndDt 를 빼면 서버가 status=ERROR 로 응답한다(실측).

    이 회귀는 조용히 실패하지 않는다 — 빠뜨리면 검색 자체가 UPSTREAM_ERROR 가 된다.
    그래서 파라미터 **존재**를 직접 못 박는다(빈 문자열이라도 있어야 한다).
    """
    label, data = await call(
        "search_tax_decisions", {"type": "audit_appeal", "query": "법인세", "limit": 2}
    )
    assert label == "OK"
    params = upstream.last_params("ASIPDM001MR01")
    assert "bltnStrtDt" in params and "bltnEndDt" in params
    assert params["lnkClCd"] == "02"
    assert params["ntstDcmTtl"] == "법인세"
    assert params["ntstDcmDscmCntn"] == ""  # 제목·요지 필드와 결정번호 필드를 섞지 않는다
    assert data["total"] == 3124
    assert data["documentType"] == "감사원 심사청구"


async def test_audit_appeal_document_number_goes_to_number_field(upstream) -> None:
    """결정번호 모양이면 제목 필드가 아니라 결정번호 필드로 보낸다(둘 다 걸면 0건)."""
    await call("search_tax_decisions", {"type": "audit_appeal", "query": "2025심사2038"})
    params = upstream.last_params("ASIPDM001MR01")
    assert params["ntstDcmDscmCntn"] == "2025심사2038"
    assert params["ntstDcmTtl"] == ""


async def test_audit_appeal_rows_are_list_only_with_attachment(upstream) -> None:
    """11 은 상세 액션이 없다 — 행에 ntstDcmId 가 없고, 본문 부재를 숨기지 않는다."""
    label, data = await call("search_tax_decisions", {"type": "audit_appeal", "limit": 2})
    assert label == "OK"
    assert data["bodyUnavailable"] is True
    item = data["items"][0]
    assert "ntstDcmId" not in item
    assert item["documentNumber"] and item["summary"]
    assert item["attachment"]["fleId"]
    assert item["attachment"]["downloadUrl"].startswith(DOWNLOAD_URL)
    # 첨부 확인을 요청하지 않았으면 확보 여부는 주장하지 않는다
    assert "available" not in item["attachment"]
    assert "ACMCMA001MR02" not in [action for action, _ in upstream.calls]


async def test_audit_appeal_attachment_status_reports_storage_gap(upstream) -> None:
    """원본 스토리지에 파일이 없으면 그대로 알린다 — 오류 페이지를 파일로 저장하지 않는다."""
    label, data = await call(
        "search_tax_decisions",
        {"type": "audit_appeal", "limit": 1, "attachment_status": True},
    )
    assert label == "OK"
    attachment = data["items"][0]["attachment"]
    assert attachment["available"] is False
    assert "원문 파일 미제공" in attachment["unavailableReason"]
    assert data["attachmentCheckedOn"] == datetime.now(timezone.utc).date().isoformat()
    assert data["attachmentGapMeasuredOn"] == special.ATTACHMENT_STORAGE_GAP_DETECTED_ON
    assert data["attachmentGapMeasured"] == special.ATTACHMENT_STORAGE_GAP_MEASURED
    # 첨부 확인은 돌려줄 행마다 요청한다. fixture 는 limit 과 무관하게 2행을 주므로
    # "마지막 호출"이 아니라 "돌려준 행이 실제로 확인됐는지"를 본다.
    probed = [
        params["fleId"]
        for action_id, params in upstream.calls
        if action_id == "ACMCMA001MR02"
    ]
    assert attachment["fleId"] in probed


async def test_audit_appeal_attachment_status_when_file_is_served(upstream) -> None:
    """파일이 있으면 available=true 이고 불가 사유를 붙이지 않는다."""
    upstream.attachment_available = True
    label, data = await call(
        "search_tax_decisions",
        {"type": "audit_appeal", "limit": 1, "attachment_status": True},
    )
    assert label == "OK"
    attachment = data["items"][0]["attachment"]
    assert attachment["available"] is True
    assert "unavailableReason" not in attachment
    assert attachment["format"] == "pdf"


async def test_attachment_probe_checks_file_bytes(upstream) -> None:
    """헤더나 크기 대신 본문의 파일 서명으로 확보 여부를 확인한다."""
    upstream.attachment_available = False
    available, reason = await special.probe_attachment(
        f"{DOWNLOAD_URL}?fleId=1&fleSn=2"
    )
    assert available is False and reason

    upstream.attachment_available = True
    available, reason = await special.probe_attachment(f"{DOWNLOAD_URL}?fleId=3&fleSn=4")
    assert available is True and reason is None


def test_error_page_detection_is_body_based() -> None:
    """확장자가 아니라 본문 선두를 본다(서버는 Content-Disposition 을 정상으로 준다)."""
    assert special._is_error_page(b'\n<!DOCTYPE html>\n<html lang="ko">')
    assert special._is_error_page(b"<HTML>")
    assert not special._is_error_page(b"%PDF-1.4\n")
    assert not special._is_error_page(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # HWP5 OLE2


# ─── 13 자주찾는 쟁점별 사례 ───────────────────────────────────────────────────

async def test_curated_issue_uses_gist_field_and_returns_detail_id(upstream) -> None:
    label, data = await call(
        "search_tax_interpretations", {"type": "curated_issue", "query": "상속", "limit": 2}
    )
    assert label == "OK"
    params = upstream.last_params("ASIQTH001MR01")
    assert params["schNtstDcmGistCntn"] == "상속"
    assert params["schNtstDcmTtl"] == ""
    assert data["total"] == 1084
    item = data["items"][0]
    assert item["ntstDcmId"].isdigit()
    assert item["issueCategory"] and item["issueCategoryCode"]
    assert item["authorityLevel"] == "nts_ruling"


async def test_curated_issue_rejects_registration_date_filter(upstream) -> None:
    """원본이 제공하지 않는 필터를 조용히 무시하지 않는다."""
    label, data = await call(
        "search_tax_interpretations",
        {"type": "curated_issue", "query": "상속", "date_from": "2024-01-01"},
    )
    assert label == "INVALID_INPUT"
    assert data["ok"] is False


# ─── 14 납세자보호위원회 심의사례 ──────────────────────────────────────────────

async def test_taxpayer_protection_forces_doc_class(upstream) -> None:
    """ntstDcmClCd 를 강제로 넣어야 한다 — 빼면 0건이 온다(실측)."""
    label, data = await call(
        "search_tax_decisions", {"type": "taxpayer_protection", "query": "세무조사", "limit": 2}
    )
    assert label == "OK"
    params = upstream.last_params("ASIPRC019MR02")
    assert params["ntstDcmClCd"] == "14"
    assert params["searchKeyword"] == "세무조사"
    assert params["searchCondition"] == "ntstDcmTtl"
    assert data["total"] == 165
    assert data["items"][0]["ntstDcmId"].isdigit()


async def test_taxpayer_protection_detail_uses_own_source_page(upstream) -> None:
    """14 상세는 공용 상세 액션을 쓰되 원문 링크와 도메인이 14 를 가리켜야 한다."""
    label, data = await call(
        "get_tax_document", {"ntst_dcm_id": "200000000000022050", "detail": "compact"}
    )
    assert label == "OK"
    document = data["document"]
    assert document["domain"] == "decision"
    assert document["documentType"] == "납세자보호위원회심의사례"
    assert document["documentNumber"] == "세무서납보-2025-004"
    assert document["authorityLevel"] == "adjudication"
    assert "/bg/USEBGF001P.do?ntstDcmId=200000000000022050" in document["sourceUrl"]


async def test_taxpayer_protection_claimant_section_is_parsed(upstream) -> None:
    """심의사례는 주체를 '요청법인'으로 쓴다 — 절 이름이 사라지면 판단 근거를 잃는다."""
    from korean_taxlaw_mcp.html_text import parse_body_html

    html = load("special_taxpayer_protection_detail")["dcmHwpEditorDVOList"]
    body = next(x["dcmFleByte"] for x in html if str(x["dcmFleTy"]).lower() == "html")
    sections = parse_body_html(body).sections
    assert "claimantView" in sections
    assert "agencyView" in sections
    assert "reasoning" in sections


async def test_special_types_reject_decision_filters(upstream) -> None:
    """05~10 전용 필터를 11·14 에 조용히 적용하지 않는다."""
    for document_type in ("audit_appeal", "taxpayer_protection"):
        label, data = await call(
            "search_tax_decisions",
            {"type": document_type, "query": "세무조사", "result": ["기각"]},
        )
        assert label == "INVALID_INPUT", document_type
        assert data["ok"] is False


# ─── 라우팅 ────────────────────────────────────────────────────────────────────

def test_router_separates_audit_appeal_from_nts_review() -> None:
    """'감사원 심사청구'는 국세청 심사청구(07)와 낱말이 겹친다 — 11 로만 좁힌다."""
    hint = route_query("감사원 심사청구 찾아줘")
    assert hint.doc_classes == ["11"]
    assert hint.domains == ["decision"]
    assert hint.content_query == ""

    nts = route_query("심사청구 부가가치세")
    assert nts.doc_classes == ["07"]


def test_router_recognizes_new_source_phrases() -> None:
    assert route_query("납세자보호위원회 심의사례").doc_classes == ["14"]
    assert route_query("자주찾는 쟁점별 사례 배우자상속공제").doc_classes == ["13"]
    assert route_query("자주찾는 쟁점별 사례 배우자상속공제").content_query == "배우자상속공제"
