"""Opt-in proof for the ordinary NTS list response; never infer from NOT_FOUND.

ASIPDI002PR01 reports counts as a sparse facet map (zero-count classes are
omitted), rather than a total field. The empty form is pinned by public fixtures.
Missing top/categoryMap/SUB_ID_CATEGORY/body is not that empty form.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .errors import ErrorCode, NtsError

CONTRACT = "nts-document-list-v1"


def enabled() -> bool:
    return os.environ.get("TAXLAW_SEARCH_PROOF_V1") == "1"


def invalid() -> None:
    raise NtsError(ErrorCode.PARSE_ERROR, "국세청 검색 응답 구조 또는 건수가 일치하지 않습니다.")


def inspect_list(payload: Any, wanted: set[str], page: int, limit: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        invalid()
    top, body = payload.get("top"), payload.get("body")
    if not isinstance(top, list) or len(top) != 1 or not isinstance(top[0], dict) or not isinstance(body, list):
        invalid()
    category_map = top[0].get("categoryMap")
    if not isinstance(category_map, dict) or not isinstance(category_map.get("SUB_ID_CATEGORY"), list):
        invalid()
    counts: dict[str, int] = {}
    for entry in category_map["SUB_ID_CATEGORY"]:
        if not isinstance(entry, dict):
            invalid()
        name, count = entry.get("name"), entry.get("count")
        if not isinstance(name, str) or not re.fullmatch(r"\d{3}_\d{2}", name) or name in counts:
            invalid()
        if isinstance(count, bool) or not isinstance(count, (str, int)) or not re.fullmatch(r"0|[1-9]\d{0,14}", str(count)):
            invalid()
        counts[name] = int(count)
    total = sum(counts.get(name, 0) for name in wanted)
    ids: list[str] = []
    class_ids: dict[str, set[str]] = {}
    for row in body:
        if not isinstance(row, dict) or not isinstance(row.get("dcm"), dict):
            invalid()
        dcm = row["dcm"]
        doc_id, doc_class = dcm.get("DOC_ID"), dcm.get("NTST_DCM_CL_CD")
        if not isinstance(doc_id, str) or not re.fullmatch(r"\d{18}", doc_id) or f"001_{doc_class}" not in wanted:
            invalid()
        ids.append(doc_id)
        class_ids.setdefault(f"001_{doc_class}", set()).add(doc_id)
    # Sparse omitted classes mean zero, so their rows cannot be certified.
    # Use unique IDs here: duplicate rows retain the existing partial policy.
    if any(len(doc_ids) > counts.get(name, 0) for name, doc_ids in class_ids.items()):
        invalid()
    count = len(body)
    expected = min(limit, max(0, total - (page - 1) * limit))
    if count > limit or count > total or (expected > 0 and count == 0) or (expected == 0 and count != 0):
        invalid()
    status = "partial"
    if total == 0 and page == 1:
        status = "empty_confirmed"
    elif total > 0 and (page - 1) * limit >= total:
        status = "page_out_of_range"
    elif count == expected and count > 0 and len(set(ids)) == count:
        status = "found"
    return {"contract": CONTRACT, "schema": "ASIPDI002PR01/sparse-facets-v1",
            "operation": "list_search", "page": page, "limit": limit,
            "total": total, "returned": count, "scope_verified": True, "status": status}


def bind_scope(result: dict[str, Any], unresolved: list[str]) -> dict[str, Any] | None:
    proof = result.get("searchProof")
    if not proof:
        return None
    return {**proof, "scope_verified": not unresolved,
            "status": "partial" if unresolved else proof["status"]}
