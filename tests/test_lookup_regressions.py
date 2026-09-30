"""후보가 페이지로 나뉘어도 중복·조회 실패를 확정 응답으로 바꾸지 않는다."""
from copy import deepcopy

import pytest

from korean_taxlaw_mcp.domains import lookup

from .test_tools import _duplicate_document_number_payload, call, upstream

NUMBER = "법인46012-1784"


def _page(rows: list[dict], total: int) -> dict:
    payload = _duplicate_document_number_payload()
    payload["body"] = rows
    payload["top"][0]["categoryMap"]["SUB_ID_CATEGORY"][0]["count"] = str(total)
    return payload


def _other_rows(count: int, offset: int = 0) -> list[dict]:
    rows = []
    for index in range(offset, offset + count):
        row = deepcopy(_duplicate_document_number_payload()["body"][0])
        row["dcm"].update(DOC_ID=str(200000000000000000 + index),
                          NTST_DCM_DSCM_CNTN=f"법인46012-{3000 + index}")
        rows.append(row)
    return rows


@pytest.mark.parametrize("first_page_has_exact", [True, False])
async def test_lookup_checks_next_page_before_resolving(upstream, first_page_has_exact):
    first, second = _duplicate_document_number_payload()["body"]
    rows = [first, *_other_rows(29)] if first_page_has_exact else _other_rows(30)
    upstream.search_pages[(NUMBER, 1)] = _page(rows, 31)
    upstream.search_pages[(NUMBER, 2)] = _page([second], 31)

    outcome = await lookup.lookup_by_document_number(NUMBER, metadata_only=True)

    if first_page_has_exact:
        assert outcome["ambiguous"] is True
        assert outcome["candidateCount"] == 2
    else:
        assert outcome["found"] is True
        assert outcome["document"]["ntstDcmId"] == second["dcm"]["DOC_ID"]
    assert [param["startCount"] for _, param in upstream.calls] == [1, 2]


@pytest.mark.parametrize("first_page_has_exact", [True, False])
async def test_context_search_checks_next_page_before_resolving(upstream, first_page_has_exact):
    original = _duplicate_document_number_payload()
    first, second = original["body"]
    upstream.search_payload[NUMBER] = original
    query = f"{NUMBER} 퇴직금"
    rows = [first, *_other_rows(99)] if first_page_has_exact else _other_rows(100)
    upstream.search_pages[(query, 1)] = _page(rows, 101)
    upstream.search_pages[(query, 2)] = _page([second], 101)

    outcome = await lookup.lookup_by_document_number(NUMBER, context_query="퇴직금", metadata_only=True)

    if first_page_has_exact:
        assert outcome["ambiguous"] is True
        assert outcome["candidateCount"] == 2
    else:
        assert outcome["resolvedBy"] == "document_number_and_context"
        assert outcome["document"]["ntstDcmId"] == second["dcm"]["DOC_ID"]


@pytest.mark.parametrize("second_page", ["empty", "repeated"])
async def test_broken_pagination_does_not_claim_uniqueness(upstream, second_page):
    first = _duplicate_document_number_payload()["body"][0]
    rows = [first, *_other_rows(29)]
    upstream.search_pages[(NUMBER, 1)] = _page(rows, 31)
    upstream.search_pages[(NUMBER, 2)] = _page([] if second_page == "empty" else rows, 31)

    label, data = await call("lookup_tax_document", {"document_number": NUMBER})

    assert label == "UPSTREAM_ERROR"
    assert "부존재로 단정하지" in data["guardrail"]
    assert all(action != "ASIQTB002PR01" for action, _ in upstream.calls)


async def test_lookup_page_limit_is_explicit_incomplete_result(upstream, monkeypatch):
    monkeypatch.setattr(lookup, "_MAX_SEARCH_PAGES", 2)
    first = _duplicate_document_number_payload()["body"][0]
    upstream.search_pages[(NUMBER, 1)] = _page([first, *_other_rows(29)], 61)
    upstream.search_pages[(NUMBER, 2)] = _page(_other_rows(30, offset=29), 61)

    label, data = await call("lookup_tax_document", {"document_number": NUMBER})

    assert label == "LOOKUP_INCOMPLETE"
    assert data["error"]["detail"]["pagesChecked"] == 2
    assert all(action != "ASIQTB002PR01" for action, _ in upstream.calls)


@pytest.mark.parametrize("number", [NUMBER, "서면-2026-법규재산-0119"])
async def test_unified_search_preserves_duplicate_candidates(upstream, number):
    payload = _duplicate_document_number_payload()
    for row in payload["body"]:
        row["dcm"]["NTST_DCM_DSCM_CNTN"] = number
    upstream.search_payload[number] = payload

    label, data = await call("search_taxlaw", {"query": number, "limit_per_domain": 1})

    assert label == "AMBIGUOUS_DOCUMENT_NUMBER"
    assert data["error"]["detail"]["candidateCount"] == 2
    assert len(data["error"]["detail"]["candidates"]) == 2
    assert len(upstream.calls) == 1  # ambiguity must not fall back to keyword search


async def test_unified_search_resolves_legacy_number_with_context(upstream):
    original = _duplicate_document_number_payload()
    upstream.search_payload[NUMBER] = original
    upstream.search_payload[f"{NUMBER} 퇴직금"] = _page([original["body"][1]], 1)

    label, data = await call("search_taxlaw", {"query": f"{NUMBER} 퇴직금"})

    assert label == "OK"
    assert data["exactMatch"] is True
    assert data["resolvedBy"] == "document_number_and_context"
    assert data["candidateCount"] == 2
    assert data["document"]["ntstDcmId"] == "010000000000062896"
    assert len(upstream.calls) == 2
