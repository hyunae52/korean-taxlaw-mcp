"""Boundary regressions for the special document types; no live requests."""
from __future__ import annotations

import httpx
import pytest

from korean_taxlaw_mcp.domains import special
from korean_taxlaw_mcp.errors import ErrorCode, NtsError

from .test_special import call, upstream  # shared fixture with real response shapes


@pytest.mark.parametrize("tool,args", [
    ("search_tax_interpretations", {"type": "curated_issue", "match": "any"}),
    ("search_tax_interpretations", {"type": "curated_issue", "exclude": ["증여"]}),
    ("search_tax_interpretations", {"type": "curated_issue", "sort": "oldest"}),
    ("search_tax_interpretations", {"type": "curated_issue", "law": "상속세 및 증여세법"}),
    ("search_tax_interpretations", {"type": "curated_issue", "article": "제19조"}),
    ("search_tax_interpretations", {"type": "curated_issue", "tax_type": ["소득세", "법인세"]}),
    ("search_tax_decisions", {"type": "audit_appeal", "match": "any"}),
    ("search_tax_decisions", {"type": "audit_appeal", "exclude": ["증여"]}),
    ("search_tax_decisions", {"type": "audit_appeal", "sort": "oldest"}),
    ("search_tax_decisions", {"type": "audit_appeal", "tax_type": "법인세"}),
    ("search_tax_decisions", {"type": "taxpayer_protection", "tax_type": "법인세"}),
    ("search_tax_decisions", {"type": "taxpayer_protection", "exclude": ["증여"]}),
    ("search_tax_decisions", {"type": "taxpayer_protection", "attachment_status": True}),
    ("search_tax_decisions", {"type": "court", "attachment_status": True}),
    ("search_tax_decisions", {"type": "audit_appeal", "case_number": "2025심사2038"}),
    ("search_tax_decisions", {"type": "taxpayer_protection", "case_number": "세무서납보-2025-004"}),
])
async def test_special_filters_are_never_silently_dropped(upstream, tool, args):
    label, result = await call(tool, {"query": "상속", **args})
    assert label == "INVALID_INPUT", (args, label, result)
    assert not upstream.calls, "Unsupported filters must fail before retrieval"


@pytest.mark.parametrize("payload", [
    {},
    {"recordCount": 1, "badiRvwDVOList": "changed schema"},
    {"recordCount": 1, "badiRvwDVOList": ["not an object"]},
    {"recordCount": "invalid", "badiRvwDVOList": []},
])
async def test_broken_special_response_is_not_document_absence(upstream, payload):
    upstream.payload["ASIPDM001MR01"] = payload
    label, result = await call("search_tax_decisions", {"type": "audit_appeal"})
    assert label == "UPSTREAM_ERROR", (label, result)


@pytest.mark.parametrize("kind,action,key", [
    ("audit_appeal", "ASIPDM001MR01", "badiRvwDVOList"),
    ("taxpayer_protection", "ASIPRC019MR02", "dcmBscDVOList"),
])
async def test_nonzero_count_empty_page_is_not_document_absence(upstream, kind, action, key):
    upstream.payload[action] = {"recordCount": 5, key: []}
    label, result = await call("search_tax_decisions", {"type": kind})
    assert label == "UPSTREAM_ERROR", result
    # A page past the end of a valid result set may legitimately be empty.
    label, result = await call("search_tax_decisions", {"type": kind, "page": 2, "limit": 5})
    assert label == "NOT_FOUND", result


@pytest.mark.parametrize("kind", ["audit_appeal", "taxpayer_protection"])
@pytest.mark.parametrize("field", ["date_from", "date_to"])
@pytest.mark.parametrize("date", ["2026-02-30", "2026/09/30", "not-a-date"])
async def test_invalid_special_date_is_rejected_before_search(upstream, kind, field, date):
    label, result = await call("search_tax_decisions", {"type": kind, field: date})
    assert label == "INVALID_INPUT", result
    assert not upstream.calls


@pytest.mark.parametrize("kind,action", [
    ("audit_appeal", "ASIPDM001MR01"),
    ("taxpayer_protection", "ASIPRC019MR02"),
])
async def test_valid_special_dates_are_applied(upstream, kind, action):
    label, result = await call("search_tax_decisions", {
        "type": kind, "date_from": "2026-02", "date_to": "20260930",
    })
    assert label == "OK", result
    params = upstream.last_params(action)
    assert params["bltnStrtDt"] == "20260201"
    assert params["bltnEndDt"] == "20260930"


@pytest.mark.parametrize("query,action,field,text,domain", [
    ("감사원 심사청구 법인세", "ASIPDM001MR01", "ntstDcmTtl", "법인세", "decision"),
    ("납세자 보호 위원회 심의 사례 세무조사", "ASIPRC019MR02", "searchKeyword", "세무조사", "decision"),
    ("납보위 세무조사", "ASIPRC019MR02", "searchKeyword", "세무조사", "decision"),
    ("자주 찾는 쟁점별 사례 상속", "ASIQTH001MR01", "schNtstDcmGistCntn", "상속", "interpretation"),
    ("감사원 심사청구 2025심사2038", "ASIPDM001MR01", "ntstDcmDscmCntn", "2025심사2038", "decision"),
    ("감사원 심사청구 2011감심200", "ASIPDM001MR01", "ntstDcmDscmCntn", "2011감심200", "decision"),
    ("감사원 심사청구 찾아줘", "ASIPDM001MR01", "ntstDcmTtl", "", "decision"),
])
async def test_unified_search_uses_the_requested_source(upstream, query, action, field, text, domain):
    label, result = await call("search_taxlaw", {"query": query, "limit_per_domain": 1})
    assert label == "OK", result
    assert result["results"][domain]["items"]
    assert len(result["results"][domain]["items"]) == 1
    assert [name for name, _ in upstream.calls] == [action]
    assert upstream.last_params(action)[field] == text


@pytest.mark.parametrize("args", [
    {"query": "감사원 심사청구 법인세", "tax_type": "법인세"},
    {"query": "납세자보호위원회 세무조사", "tax_type": "법인세"},
    {"query": "쟁점별 사례 상속", "tax_type": ["소득세", "법인세"]},
    {"query": "자주찾는 쟁점별 사례"},
])
async def test_unified_special_search_preserves_filter_contract(upstream, args):
    label, result = await call("search_taxlaw", args)
    assert label == "INVALID_INPUT", result
    assert not upstream.calls


async def test_unified_search_keeps_each_special_source_metadata(upstream):
    label, result = await call("search_taxlaw", {"query": "감사원 심사청구 납세자보호위원회 세무조사"})
    assert label == "OK", result
    sources = result["results"]["decision"]
    assert sources["audit_appeal"]["bodyUnavailable"] is True
    assert sources["audit_appeal"]["items"][0]["attachment"]
    assert sources["taxpayer_protection"]["items"][0]["ntstDcmId"]
    assert {name for name, _ in upstream.calls} == {"ASIPDM001MR01", "ASIPRC019MR02"}


async def test_unified_search_combines_standard_and_special_sources(upstream):
    from .conftest import load
    upstream.payload["ASIPDI002PR01"] = load("search_court")
    label, result = await call("search_taxlaw", {"query": "감사원 심사청구 판례 법인세"})
    assert label == "OK", result
    sources = result["results"]["decision"]
    assert sources["standard"]["items"]
    assert sources["audit_appeal"]["items"]
    assert upstream.last_params("ASIPDI002PR01")["dcmClCdCtl"] == ["001_09"]


async def test_default_unified_search_does_not_expand_to_special_sources(upstream):
    await call("search_taxlaw", {"query": "법인세"})
    assert {name for name, _ in upstream.calls} == {"ASIPDI002PR01"}


async def test_unified_source_failure_preserves_other_results(upstream):
    upstream.payload["ASIPDM001MR01"] = {}  # malformed audit source response
    label, result = await call("search_taxlaw", {"query": "감사원 심사청구 납세자보호위원회 세무조사"})
    assert label == "OK", result
    assert result["results"]["decision"]["taxpayer_protection"]["items"]
    assert "decision.audit_appeal" in result["partialErrors"]


async def test_unified_special_failure_is_not_absence(upstream):
    upstream.payload["ASIPDM001MR01"] = {}
    label, result = await call("search_taxlaw", {"query": "감사원 심사청구 법인세"})
    assert label == "UPSTREAM_ERROR", result


async def test_attachment_missing_serial_keeps_search_result(upstream):
    row = upstream.payload["ASIPDM001MR01"]["badiRvwDVOList"][0]
    row.pop("fleSn", None)
    label, result = await call("search_tax_decisions", {
        "type": "audit_appeal", "limit": 1, "attachment_status": True,
    })
    assert label == "OK", result
    attachment = result["items"][0]["attachment"]
    assert attachment.get("available") is None
    assert attachment["statusError"] == "INVALID_INPUT"


async def test_special_result_respects_requested_page_size(upstream):
    label, result = await call("search_tax_decisions", {"type": "audit_appeal", "limit": 1})
    assert label == "OK", result
    assert len(result["items"]) == 1


@pytest.mark.parametrize("content_type,body", [
    ("text/html", b"<!DOCTYPE html><html>maintenance</html>"),
    ("application/octet-stream", b"<!DOCTYPE html><html>" + b"x" * 8192),
    ("application/pdf", b"\xef\xbb\xbf  <html>not a PDF</html>"),
], ids=["html", "large-html", "bom-html"])
async def test_html_attachment_is_not_available(monkeypatch, content_type, body):
    def respond(request):
        return httpx.Response(200, headers={
            "content-type": content_type, "content-length": str(len(body)),
        }, content=b"" if request.method == "HEAD" else body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async def get_client():
            return client
        monkeypatch.setattr(special, "get_client", get_client)
        available, reason = await special.probe_attachment(special.attachment_url("1", "1"))
        assert available is False
        assert reason


@pytest.mark.parametrize("status,body", [
    (503, b"temporarily unavailable"),
    (403, b"request denied"),
    (302, b""),
    (200, b""),
    (200, b'{"error":"maintenance"}'),
])
async def test_attachment_uncertainty_is_an_error_not_file_absence(monkeypatch, status, body):
    def respond(request):
        return httpx.Response(status, headers={"content-type": "application/octet-stream"},
                              content=b"" if request.method == "HEAD" else body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async def get_client():
            return client
        monkeypatch.setattr(special, "get_client", get_client)
        with pytest.raises(NtsError) as error:
            await special.probe_attachment(special.attachment_url("1", "1"))
        assert error.value.code == ErrorCode.UPSTREAM_ERROR


async def test_attachment_verification_reads_only_a_bounded_prefix(monkeypatch):
    reads = []
    class FileStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            reads.append("prefix")
            yield b"%PDF-1.4\n" + b"x" * 8192
            raise AssertionError("Attachment verification read beyond the bounded prefix")

    def respond(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={
                "content-type": "application/octet-stream", "content-length": "10000000",
            })
        return httpx.Response(200, headers={"content-type": "application/octet-stream"},
                              stream=FileStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async def get_client():
            return client
        monkeypatch.setattr(special, "get_client", get_client)
        assert await special.probe_attachment(special.attachment_url("1", "1")) == (True, None)
        assert reads == ["prefix"], "Availability must be based on bytes, not just HEAD headers"
