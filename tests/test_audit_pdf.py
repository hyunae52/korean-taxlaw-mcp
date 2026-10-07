"""PDF retrieval contracts through the real MCP tool and isolated pypdf worker."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys

import httpx
import pytest
import respx

from korean_taxlaw_mcp.action_client import ACTION_URL, close_client
from korean_taxlaw_mcp.domains import audit_pdf
from korean_taxlaw_mcp.errors import ErrorCode, NtsError
from korean_taxlaw_mcp.pdf_text import extract_pdf

from .pdf_fixture import pdf_bytes
from .test_tools import call

NUMBER = "2024심사636"
URL = "https://taxlaw.nts.go.kr/downloadFile.do?fleId=300000000001139785&fleSn=969300"


@pytest.fixture
async def pdf_source():
    class Source:
        data = pdf_bytes(["감사원 심사결정 2024-심사-636", "주문: 심사청구를 기각한다.", "이유: 관계 법령을 검토한다."])
        downloads = 0
        status = 200
        headers = {}
        params = None
        total = 1
        rows = [{"ntstDcmDscmCntn": NUMBER, "rgtDt": "20251126", "ntstDcmTtl": "공개 결정문",
                 "fleId": "300000000001139785", "fleSn": "969300", "ntstDcmGistCntn": "검색 요지"}]

        def action(self, request):
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["actionId"] == "ASIPDM001MR01"
            self.params = json.loads(form["paramData"])
            return httpx.Response(200, json={"status": "SUCCESS", "data": {form["actionId"]: {
                "recordCount": self.total, "badiRvwDVOList": self.rows}}})

        def download(self, request):
            self.downloads += 1
            assert "range" not in request.headers  # Full download, not the existing prefix-only probe.
            assert request.headers["accept-encoding"] == "identity"
            return httpx.Response(self.status, headers=self.headers, content=self.data)

    source = Source()
    # Avoid a shared mutable class fixture leaking modified identifiers between cases.
    source.rows = [dict(row) for row in Source.rows]
    with respx.mock(assert_all_called=False) as mock:
        mock.post(ACTION_URL).mock(side_effect=source.action)
        mock.get(URL).mock(side_effect=source.download)
        yield source
    await close_client()


async def test_mcp_reads_korean_pdf_and_binds_offsets_to_physical_pages(pdf_source):
    label, result = await call("get_tax_document", {"document_number": "2024-심사-636"})
    assert label == "OK", result
    doc = result["document"]
    assert pdf_source.params["ntstDcmDscmCntn"] == NUMBER
    assert doc["documentNumber"] == NUMBER and doc["decisionDate"] == "2025-11-26"
    assert doc["sourceUrl"] == URL and doc["pageCount"] == 3
    assert doc["bodyUnavailable"] is False and doc["bodyPartial"] is False
    assert doc["completeness"] == "unverified" and doc["extractionMethod"] == "pdf_text_layer"
    assert "nextPage" not in doc
    for page, text in zip(doc["pages"], ["감사원 심사결정", "주문:", "이유:"]):
        assert text in doc["fullText"][page["start"]:page["end"]]
        assert page["sourceUrl"] == f"{URL}#page={page['pageNumber']}"
    assert doc["attachment"]["sha256"] == hashlib.sha256(pdf_source.data).hexdigest()
    assert doc["attachment"]["downloadedBytes"] == len(pdf_source.data)


async def test_supplementary_unicode_offsets_are_codepoints_not_utf16_units(pdf_source):
    pdf_source.data = pdf_bytes(["가😀나", "끝"])
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "OK", result
    doc = result["document"]
    assert doc["fullText"] == "가😀나\n\n끝" and doc["offsetUnit"] == "unicode_codepoint"
    assert [(p["start"], p["end"]) for p in doc["pages"]] == [(0, 3), (5, 6)]
    assert len("가😀나".encode("utf-16-le")) // 2 == 4


async def test_lookup_routes_audit_numbers_to_the_pdf_path(pdf_source):
    label, result = await call("lookup_tax_document", {"document_number": "2024 심사 636"})
    assert label == "OK", result
    assert result["exactMatch"] is True
    assert "심사청구를 기각" in result["document"]["fullText"]


def test_oversized_audit_number_never_reaches_the_matcher(monkeypatch):
    class RejectCalls:
        def fullmatch(self, value):
            raise AssertionError("Unbounded input reached the audit matcher")
    monkeypatch.setattr(audit_pdf, "_NUMBER", RejectCalls())
    assert audit_pdf.audit_number("2024" + " " * 100_000 + "X") is None


async def test_oversized_audit_lookup_fails_before_source_search(monkeypatch):
    async def reject(**kwargs):
        raise AssertionError("Invalid audit number reached the source")
    monkeypatch.setattr(audit_pdf, "search_special_documents", reject)
    with pytest.raises(NtsError) as error:
        await audit_pdf.get_audit_document("2024" + " " * 100_000 + "X")
    assert error.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("raw", ["2024심사636", "2024-심사-636", " 2024 - 심사 - 000636 "])
def test_bounded_audit_number_keeps_supported_variants(raw):
    assert audit_pdf.audit_number(raw) == NUMBER


@pytest.mark.parametrize("args", [{"include_full_text": False}, {"detail": "compact"}])
async def test_metadata_only_never_downloads_the_attachment(pdf_source, args):
    label, result = await call("get_tax_document", {"document_number": NUMBER, **args})
    assert label == "OK", result
    assert pdf_source.downloads == 0
    assert result["document"]["bodyNotRequested"] is True
    assert "fullText" not in result["document"]


async def test_existing_search_remains_discovery_only(pdf_source):
    label, result = await call("search_tax_decisions", {"type": "audit_appeal", "query": NUMBER})
    assert label == "OK", result
    assert pdf_source.downloads == 0 and result["bodyUnavailable"] is True
    assert "get_tax_document" in result["bodyNote"]


async def test_page_range_returns_only_requested_pages_and_next_page(pdf_source):
    label, result = await call("get_tax_document", {"document_number": NUMBER, "page_start": 2, "page_end": 2})
    assert label == "OK", result
    doc = result["document"]
    assert doc["bodyPartial"] is True and doc["nextPage"] == 3
    assert [p["pageNumber"] for p in doc["pages"]] == [2]
    assert "감사원" not in doc["fullText"] and "주문:" in doc["fullText"]


@pytest.mark.parametrize("args", [
    {"page_start": 3, "page_end": 2}, {"page_end": 21},
    {"page_start": 2, "include_full_text": False}, {"page_start": 2, "detail": "compact"},
    {"ntst_dcm_id": "200000000000022584"},
])
async def test_invalid_or_conflicting_pdf_arguments_fail_before_download(pdf_source, args):
    label, result = await call("get_tax_document", {"document_number": NUMBER, **args})
    assert label == "INVALID_INPUT", result
    assert pdf_source.downloads == 0


async def test_standard_document_does_not_silently_ignore_pdf_page_arguments():
    label, result = await call("get_tax_document", {"ntst_dcm_id": "200000000000022584", "page_end": 2})
    assert label == "INVALID_INPUT", result


async def test_requested_page_beyond_actual_pdf_is_invalid(pdf_source):
    label, result = await call("get_tax_document", {"document_number": NUMBER, "page_start": 4})
    assert label == "INVALID_INPUT", result


async def test_contradictory_source_count_does_not_establish_a_unique_document(pdf_source):
    pdf_source.total = 0
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "UPSTREAM_ERROR", result
    assert pdf_source.downloads == 0


async def test_raw_overfull_rows_cannot_hide_an_exact_duplicate(pdf_source):
    first = dict(pdf_source.rows[0])
    pdf_source.rows = [first] + [dict(first, ntstDcmDscmCntn=f"2024심사{700 + i}") for i in range(99)]
    pdf_source.rows.append(dict(first, fleSn="969301"))
    pdf_source.total = 100
    label, result = await call("lookup_tax_document", {"document_number": NUMBER})
    assert label == "UPSTREAM_ERROR", result
    assert pdf_source.downloads == 0


async def test_normal_empty_audit_search_is_still_not_found(pdf_source):
    pdf_source.rows, pdf_source.total = [], 0
    label, result = await call("lookup_tax_document", {"document_number": NUMBER})
    assert label == "NOT_FOUND", result
    assert pdf_source.downloads == 0


async def test_audit_lookup_does_not_silently_ignore_a_context_filter(pdf_source):
    label, result = await call("lookup_tax_document", {"document_number": NUMBER, "context_query": "법인세"})
    assert label == "INVALID_INPUT", result
    assert pdf_source.params is None


@pytest.mark.parametrize("mode,expected", [("different", "NOT_FOUND"), ("duplicate", "AMBIGUOUS_DOCUMENT_NUMBER"),
                                          ("incomplete", "LOOKUP_INCOMPLETE"), ("bad_id", "DETAIL_NOT_AVAILABLE")])
async def test_pdf_is_never_read_without_an_exact_unambiguous_source_identity(pdf_source, mode, expected):
    if mode == "different":
        pdf_source.rows[0]["ntstDcmDscmCntn"] = "2024심사637"
    elif mode == "duplicate":
        pdf_source.rows.append(dict(pdf_source.rows[0]))
        pdf_source.total = 2
    elif mode == "incomplete":
        pdf_source.total = 2
    else:
        pdf_source.rows[0]["fleId"] = "https://example.invalid/private"
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == expected, result
    assert pdf_source.downloads == 0


@pytest.mark.parametrize("body,code,reason", [
    (b"<!DOCTYPE html><html>temporary error</html>", "UPSTREAM_ERROR", None),
    (b"", "UPSTREAM_ERROR", None),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "DETAIL_NOT_AVAILABLE", "UNSUPPORTED_ATTACHMENT_FORMAT"),
    (b"%PDF-1.4\ntruncated", "PARSE_ERROR", "INVALID_PDF"),
])
async def test_non_pdf_and_incomplete_downloads_do_not_become_successful_body_reads(pdf_source, body, code, reason):
    pdf_source.data = body
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == code, result
    detail = result["error"]["detail"]
    assert detail["document"]["bodyUnavailable"] is True
    assert detail["document"]["sourceUrl"] == URL
    assert "fullText" not in detail["document"]
    if reason:
        assert detail["reason"] == reason


@pytest.mark.parametrize("status,code", [(302, "UPSTREAM_ERROR"), (403, "UPSTREAM_ERROR"),
                                         (503, "UPSTREAM_ERROR"), (404, "DETAIL_NOT_AVAILABLE"),
                                         (206, "UPSTREAM_ERROR")])
async def test_download_errors_and_redirects_remain_retrieval_failures(pdf_source, status, code):
    pdf_source.status = status
    pdf_source.headers = {"location": "https://example.invalid/private"}
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == code, result
    assert pdf_source.downloads == 1


async def test_empty_text_layer_is_not_mistaken_for_a_read_document(pdf_source):
    pdf_source.data = pdf_bytes([""])
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "DETAIL_NOT_AVAILABLE", result
    assert result["error"]["detail"]["reason"] == "NO_EXTRACTABLE_TEXT"
    assert result["error"]["detail"]["document"]["pagesWithoutText"] == [1]


async def test_mixed_text_and_unreadable_page_is_explicitly_partial(pdf_source):
    pdf_source.data = pdf_bytes(["읽을 수 있는 페이지", ""])
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "OK", result
    assert result["document"]["bodyPartial"] is True
    assert result["document"]["pagesWithoutText"] == [2]


@pytest.mark.parametrize("blank", ["", " " * 800], ids=["empty", "whitespace"])
async def test_blank_leading_page_does_not_hide_a_later_long_text_page(pdf_source, blank):
    pdf_source.data = pdf_bytes([blank, "가" * 800])
    label, result = await call("get_tax_document", {"document_number": NUMBER, "body_limit": 500})
    assert label == "OK", result
    doc = result["document"]
    assert doc["fullText"] == "가" * 500
    assert doc["pagesWithoutText"] == [1]
    assert doc["pages"][0]["start"] == doc["pages"][0]["end"] == 0
    assert doc["pages"][1]["pageNumber"] == doc["nextPage"] == 2
    assert doc["pages"][1]["truncated"] is True
    assert doc["bodyPartial"] is True and doc["fullTextTruncated"] is True


async def test_encrypted_pdf_returns_a_specific_limitation(pdf_source):
    pdf_source.data = pdf_bytes(["private"], encrypted=True)
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "DETAIL_NOT_AVAILABLE", result
    assert result["error"]["detail"]["reason"] == "ENCRYPTED_PDF"


async def test_busy_pdf_workers_fail_promptly_without_downloading(pdf_source, monkeypatch):
    monkeypatch.setattr(audit_pdf, "_slots", asyncio.Semaphore(0))
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label == "RATE_LIMITED", result
    assert pdf_source.downloads == 0


@pytest.mark.parametrize("headers", [{"content-length": "9000000"}, {"content-encoding": "gzip"}, {"content-length": "1"}])
async def test_transfer_size_and_encoding_must_be_verified(pdf_source, headers):
    pdf_source.headers = headers
    label, result = await call("get_tax_document", {"document_number": NUMBER})
    assert label in {"DETAIL_NOT_AVAILABLE", "UPSTREAM_ERROR"}, result


def test_text_limit_preserves_page_offsets_and_reports_retry_page():
    result = extract_pdf(pdf_bytes(["가" * 800]), 1, None, 500)
    assert len(result["fullText"]) == 500
    assert result["fullTextTruncated"] is True and result["bodyPartial"] is True
    assert result["nextPage"] == 1 and result["pages"][0]["truncated"] is True
    assert result["pages"][0]["end"] == 500


def test_page_boundary_is_preferred_to_cutting_the_next_page():
    result = extract_pdf(pdf_bytes(["가" * 400, "나" * 400]), 1, None, 500)
    assert result["fullText"] == "가" * 400
    assert result["nextPage"] == 2 and result["pages"][0]["truncated"] is False


def test_empty_page_after_exact_character_budget_is_not_reported_as_truncated():
    result = extract_pdf(pdf_bytes(["가" * 500, ""]), 1, None, 500)
    assert len(result["fullText"]) == 500
    assert result["fullTextTruncated"] is False and result["nextPage"] is None
    assert result["pagesWithoutText"] == [2]
    assert result["pages"][1]["start"] == result["pages"][1]["end"] == 500


def test_default_page_window_is_bounded_and_can_be_followed():
    data = pdf_bytes([str(i) for i in range(1, 23)])
    first = extract_pdf(data, 1, None, 30000)
    last = extract_pdf(data, first["nextPage"], None, 30000)
    assert len(first["pages"]) == 20 and first["nextPage"] == 21
    assert [p["pageNumber"] for p in last["pages"]] == [21, 22]
    assert last["nextPage"] is None and last["bodyPartial"] is True


def test_page_limit_is_not_silent_truncation():
    with pytest.raises(NtsError) as error:
        extract_pdf(pdf_bytes([""] * 201), 1, None, 30000)
    assert error.value.detail["reason"] == "PDF_PAGE_LIMIT"


async def test_parser_timeout_terminates_the_real_worker(monkeypatch):
    created = []
    real_create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await real_create(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(audit_pdf, "WORKER_TIMEOUT", 0.000001)
    with pytest.raises(NtsError) as error:
        await audit_pdf._extract(pdf_bytes(["test"]), 1, None, 30000)
    assert error.value.code == ErrorCode.TIMEOUT
    assert len(created) == 1 and created[0].returncode is not None


async def test_download_cap_closes_unknown_length_stream(monkeypatch):
    closed = []

    class File(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"%PDF-1.4" + b"x" * 16000
            yield b"y" * 16000
            raise AssertionError("Download continued after its byte budget")

        async def aclose(self):
            closed.append(True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=File()))) as client:
        async def get_client():
            return client
        monkeypatch.setattr(audit_pdf, "get_client", get_client)
        monkeypatch.setattr(audit_pdf, "MAX_PDF_BYTES", 1000)
        with pytest.raises(NtsError) as error:
            await audit_pdf._download(URL)
        assert error.value.detail["reason"] == "PDF_SIZE_LIMIT"
    assert closed


async def test_cancelling_extraction_terminates_its_worker(monkeypatch):
    created = []
    started = asyncio.Event()
    real_create = asyncio.create_subprocess_exec

    async def slow_worker(*args, **kwargs):
        process = await real_create(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        created.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_worker)
    task = asyncio.create_task(audit_pdf._extract(pdf_bytes(["test"]), 1, None, 30000))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].returncode is not None


async def test_worker_spawn_failure_is_a_structured_error(monkeypatch):
    async def denied(*args, **kwargs):
        raise PermissionError("test")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", denied)
    with pytest.raises(NtsError) as error:
        await audit_pdf._extract(pdf_bytes(["test"]), 1, None, 30000)
    assert error.value.code == ErrorCode.PARSE_ERROR


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_large_worker_output_is_reaped_and_pdf_slot_reusable(pdf_source, monkeypatch, mode):
    created, handles = [], []
    writing = asyncio.Event()
    real_create = asyncio.create_subprocess_exec
    real_reap = audit_pdf._reap_worker
    cleanup_started, allow_cleanup = asyncio.Event(), asyncio.Event()

    async def delayed_reap(process):
        cleanup_started.set()
        await allow_cleanup.wait()
        await real_reap(process)

    async def output_worker(*args, **kwargs):
        # An actual child writes well beyond a normal pipe's capacity, then
        # remains live until the request's timeout/cancellation kills it.
        handles.extend([kwargs["stdin"], kwargs["stdout"]])
        process = await real_create(sys.executable, "-c",
            "import sys,time; sys.stdout.buffer.write(b'x' * 700000); sys.stdout.buffer.flush(); time.sleep(30)",
            **kwargs)
        created.append(process)
        async with asyncio.timeout(5):
            while os.fstat(kwargs["stdout"].fileno()).st_size < 700000:
                await asyncio.sleep(0.01)
        writing.set()
        return process

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_subprocess_exec", output_worker)
        patch.setattr(audit_pdf, "_slots", asyncio.Semaphore(1))
        if mode == "timeout":
            patch.setattr(audit_pdf, "WORKER_TIMEOUT", 0.001)
        else:
            patch.setattr(audit_pdf, "_reap_worker", delayed_reap)
        task = asyncio.create_task(audit_pdf.get_audit_document(NUMBER))
        await asyncio.wait_for(writing.wait(), 5)
        assert audit_pdf._slots.locked()
        if mode == "cancel":
            task.cancel()
            await asyncio.wait_for(cleanup_started.wait(), 5)
            task.cancel()  # A second cancellation must not release the slot early.
            await asyncio.sleep(0)
            assert not task.done() and audit_pdf._slots.locked()
            allow_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 8)
        else:
            with pytest.raises(NtsError) as error:
                await asyncio.wait_for(task, 8)
            assert error.value.code == ErrorCode.TIMEOUT
        assert created[0].returncode is not None
        assert all(handle.closed for handle in handles)
        assert not audit_pdf._slots.locked()
        patch.setattr(asyncio, "create_subprocess_exec", real_create)
        patch.setattr(audit_pdf, "WORKER_TIMEOUT", 15)
        patch.setattr(audit_pdf, "_reap_worker", real_reap)
        doc = await audit_pdf.get_audit_document(NUMBER)
        assert doc["bodyUnavailable"] is False


async def test_normal_large_worker_result_is_returned_intact(monkeypatch):
    real_create = asyncio.create_subprocess_exec

    async def output_worker(*args, **kwargs):
        return await real_create(sys.executable, "-c",
            "import json,sys; sys.stdout.buffer.write(json.dumps({'ok':True,'result':{'fullText':'가'*200000}},ensure_ascii=False).encode('utf-8'))",
            **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", output_worker)
    assert (await audit_pdf._extract(b"input", 1, None, 200000))["fullText"] == "가" * 200000


async def test_worker_cleanup_has_a_separate_deadline(monkeypatch):
    class Stalled:
        returncode = None
        killed = False
        def kill(self):
            self.killed = True
        async def wait(self):
            await asyncio.Event().wait()
    process = Stalled()
    monkeypatch.setattr(audit_pdf, "WORKER_CLEANUP_TIMEOUT", 0.001)
    with pytest.raises(NtsError) as error:
        await audit_pdf._reap_worker(process)
    assert error.value.code == ErrorCode.TIMEOUT and process.killed


async def test_unavailable_temporary_storage_is_a_structured_error(monkeypatch):
    def denied():
        raise PermissionError("test")
    monkeypatch.setattr(audit_pdf, "TemporaryFile", denied)
    with pytest.raises(NtsError) as error:
        await audit_pdf._extract(b"input", 1, None, 30000)
    assert error.value.code == ErrorCode.PARSE_ERROR
