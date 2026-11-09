from copy import deepcopy
import json

import httpx
import pytest
import respx

from korean_taxlaw_mcp.action_client import ACTION_URL
from korean_taxlaw_mcp.errors import NtsError
from korean_taxlaw_mcp.search_proof import inspect_list
from .conftest import load
from .test_tools import call, upstream


def empty():
    return deepcopy(load("search_empty_question_v1")["data"]["ASIPDI002PR01"])


def positive(total=1):
    raw = deepcopy(load("search_written"))
    raw["body"] = raw["body"][:1]
    raw["top"][0]["categoryMap"]["SUB_ID_CATEGORY"] = [{"name": "001_02", "count": str(total)}]
    return raw


@pytest.mark.parametrize("name", ["search_empty_question_v1", "search_empty_precedent_v1"])
def test_public_empty_fixture_has_explicit_sparse_facet_structure(name):
    envelope = load(name)
    assert envelope["status"] == "SUCCESS"
    proof = inspect_list(envelope["data"]["ASIPDI002PR01"], {"001_02"}, 1, 2)
    assert proof["status"] == "empty_confirmed" and proof["total"] == 0


@pytest.mark.parametrize("payload", [None, "", "<html>maintenance</html>", {}, {"top": [], "body": []},
    {"top": [{}], "body": []}, {"top": [{"categoryMap": {}}], "body": []},
    {"top": [{"categoryMap": {"SUB_ID_CATEGORY": []}}]}])
def test_missing_or_wrong_structure_never_means_empty(payload):
    with pytest.raises(NtsError, match="구조"):
        inspect_list(payload, {"001_02"}, 1, 2)


@pytest.mark.parametrize("count", [None, True, -1, "", "x", "1.0", "-1", 1.5])
def test_invalid_counts_are_errors(count):
    payload = positive()
    payload["top"][0]["categoryMap"]["SUB_ID_CATEGORY"][0]["count"] = count
    with pytest.raises(NtsError):
        inspect_list(payload, {"001_02"}, 1, 2)


def test_counts_rows_and_page_are_consistent():
    payload = positive(0)
    with pytest.raises(NtsError):
        inspect_list(payload, {"001_02"}, 1, 2)
    payload = positive(12)
    payload["body"] = []
    with pytest.raises(NtsError):
        inspect_list(payload, {"001_02"}, 1, 5)
    assert inspect_list(payload, {"001_02"}, 4, 5)["status"] == "page_out_of_range"
    assert inspect_list(empty(), {"001_02"}, 2, 5)["status"] == "partial"
    assert inspect_list(positive(), {"001_02"}, 1, 2)["status"] == "found"
    payload = positive(2)
    payload["body"] *= 2
    assert inspect_list(payload, {"001_02"}, 1, 2)["status"] == "partial"


@pytest.mark.parametrize("tool,kind", [("search_tax_interpretations", "written"), ("search_tax_decisions", "court")])
async def test_mcp_empty_proof_is_opt_in_and_keeps_legacy_error_code(upstream, monkeypatch, tool, kind):
    upstream.search_payload["probe"] = empty()
    label, data = await call(tool, {"query": "probe", "type": kind, "limit": 2})
    assert label == "NOT_FOUND" and "searchProof" not in data["error"].get("detail", {})
    monkeypatch.setenv("TAXLAW_SEARCH_PROOF_V1", "1")
    label, data = await call(tool, {"query": "probe", "type": kind, "limit": 2})
    assert label == "NOT_FOUND"
    assert data["error"]["detail"]["searchProof"]["status"] == "empty_confirmed"
    assert len(upstream.calls) == 2  # Strict mode cannot reuse an unverified cache entry.


async def test_unresolved_filter_does_not_claim_scope(upstream, monkeypatch):
    monkeypatch.setenv("TAXLAW_SEARCH_PROOF_V1", "1")
    upstream.search_payload["probe"] = empty()
    label, data = await call("search_tax_interpretations", {"query": "probe", "tax_type": "unknown-tax"})
    proof = data["error"]["detail"]["searchProof"]
    assert label == "NOT_FOUND" and proof["status"] == "partial" and proof["scope_verified"] is False


async def test_positive_payload_preserved_and_exact_lookup_has_no_list_proof(upstream, monkeypatch):
    monkeypatch.setenv("TAXLAW_SEARCH_PROOF_V1", "1")
    upstream.search_payload["probe"] = positive()
    label, data = await call("search_tax_interpretations", {"query": "probe", "type": "written"})
    assert label == "OK" and data["searchProof"]["status"] == "found"
    assert data["items"][0]["ntstDcmId"] == positive()["body"][0]["dcm"]["DOC_ID"]
    label, data = await call("search_tax_interpretations", {"document_number": "서면-2026-법규재산-9999"})
    assert label == "NOT_FOUND" and "searchProof" not in data["error"].get("detail", {})


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "", "{}", "[]", "null",
    json.dumps({"data": {"ASIPDI002PR01": {"top": [], "body": []}}}),
    json.dumps({"status": "FAILURE", "data": {"ASIPDI002PR01": {"top": [], "body": []}}})])
async def test_bad_http_200_is_never_not_found(monkeypatch, body):
    monkeypatch.setenv("TAXLAW_SEARCH_PROOF_V1", "1")
    with respx.mock(assert_all_called=False) as mock:
        mock.post(ACTION_URL).respond(200, text=body)
        label, data = await call("search_tax_interpretations", {"query": "probe"})
    assert label in {"PARSE_ERROR", "UPSTREAM_ERROR"}
    assert "searchProof" not in data["error"].get("detail", {})
    if '"status": "FAILURE"' in body:
        assert label == "UPSTREAM_ERROR"
