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
