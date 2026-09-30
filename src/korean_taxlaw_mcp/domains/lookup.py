"""문서번호 exact lookup.

이 서버의 핵심 기능이다. 사이트가 돌려준 문서번호가 입력과 정확히 같을 때만
``found``로 처리한다.

사이트 검색은 부분일치 결과도 돌려준다. ``법규재산-0119`` 를 넣으면
``서면-2026-법규재산-0119`` 와 ``기준-2023-법규부가-0044`` 와 ``서면-2015-징세-0119``
가 함께 온다(실측). 이 중 첫 건을 정답처럼 반환하면 세무 상담에서 곧바로 오답이 된다.
그래서 비슷한 건은 ``similarDocuments`` 로 따로 담고 ``exactMatch: false`` 를 명시한다.
"""

from __future__ import annotations

from typing import Any

from ..action_client import detail_url
from ..codes import collection_for
from ..config import DEFAULT_SIMILAR_LIMIT
from ..payload import slim
from ..doc_number import is_same_doc_number, lookup_candidates, parse_doc_number
from .documents import DECISION_CLASSES, INTERPRETATION_CLASSES, get_document, search_documents

_CLASSES: dict[str, list[str]] = {
    "interpretation": list(INTERPRETATION_CLASSES),
    "decision": list(DECISION_CLASSES),
}


def _domain_order(raw: str) -> list[str]:
    """문서번호 모양에서 어느 영역을 먼저 볼지 정한다. 틀려도 나머지를 순차로 본다."""
    parsed = parse_doc_number(raw)
    if parsed.inferred_doc_class:
        if collection_for(parsed.inferred_doc_class).startswith("question"):
            return ["interpretation", "decision"]
        return ["decision", "interpretation"]
    return ["interpretation", "decision"]


#: 유사문서는 '이런 별개 문서가 있다'는 신호만 주면 된다 — 식별 필드만 남긴다.
_SIMILAR_KEYS = ("documentType", "documentNumber", "title", "registrationDate", "ntstDcmId")


async def lookup_by_document_number(
    raw: str,
    *,
    context_query: str | None = None,
    include_full_text: bool = True,
    body_limit: int | None = None,
    similar_limit: int = DEFAULT_SIMILAR_LIMIT,
    metadata_only: bool = False,
) -> dict[str, Any]:
    """문서번호로 문서를 찾는다.

    후보 검색어를 순서대로 던지고, 각 응답에서 문서번호가 정확히 일치하는 건만
    exact 로 인정한다. 어느 후보에서도 exact 가 없으면 그동안 모은 부분일치 건을
    유사문서로 돌려준다.

    ``metadata_only=True`` 면 상세 조회를 생략하고 검색 요약만 돌려준다 —
    통합검색처럼 존재 확인이 목적일 때 본문 왕복과 컨텍스트를 아낀다.
    """
    parsed = parse_doc_number(raw)
    candidates = lookup_candidates(raw)
    similar: dict[str, dict[str, Any]] = {}
    exact: dict[str, tuple[str, dict[str, Any]]] = {}
    tried: list[str] = []
    searched: list[str] = []

    for domain in _domain_order(raw):
        searched.append(domain)
        domain_has_exact = False
        for candidate in candidates:
            tried.append(f"{domain}:{candidate}")
            # 후보 하나라도 조회에 실패하면 부존재를 확정할 수 없다. 장애를 삼키고
            # NOT_FOUND 로 내리면 실재하는 문서를 없다고 답하게 되므로 그대로 전파한다.
            result = await search_documents(
                doc_classes=_CLASSES[domain],
                query=candidate,
                match="all",
                limit=30,
                sort="relevance",
            )

            for item in result["items"]:
                number = item.get("documentNumber", "")
                # DOC_ID 가 빈 행은 요약 단계에서 키가 걸러진다 — 상세 조회도
                # 유사문서 수집도 불가능하므로 건너뛴다.
                if not item.get("ntstDcmId"):
                    continue
                if is_same_doc_number(number, parsed.canonical) or is_same_doc_number(number, raw):
                    exact.setdefault(item["ntstDcmId"], (domain, item))
                    domain_has_exact = True
                    continue
                if number:
                    similar.setdefault(item["ntstDcmId"], item)

            # 한 검색 응답에 같은 번호의 모든 문서가 함께 온다. 표기 변형을 더
            # 조회하면 같은 후보만 반복되므로 이 영역의 후보 검색을 끝낸다.
            if domain_has_exact:
                break

        # 문서번호 모양으로 먼저 고른 영역에서 exact 를 찾았으면 반대 영역까지
        # 조회하지 않는다. 기존 라우팅 계약을 유지하면서 같은 영역의 중복만 잡는다.
        if domain_has_exact:
            break

    selected_id: str | None = next(iter(exact)) if len(exact) == 1 else None
    resolved_by_context = False

    if len(exact) > 1 and context_query and context_query.strip():
        narrowed: set[str] = set()
        for domain in {domain for domain, _item in exact.values()}:
            result = await search_documents(
                doc_classes=_CLASSES[domain],
                query=f"{parsed.canonical} {context_query.strip()}",
                match="all",
                limit=100,
                sort="relevance",
            )
            for item in result["items"]:
                doc_id = item.get("ntstDcmId")
                if doc_id in exact and (
                    is_same_doc_number(item.get("documentNumber", ""), parsed.canonical)
                    or is_same_doc_number(item.get("documentNumber", ""), raw)
                ):
                    narrowed.add(doc_id)
        if len(narrowed) == 1:
            selected_id = next(iter(narrowed))
            resolved_by_context = True

    if selected_id:
        domain, item = exact[selected_id]
        if metadata_only:
            # 검색 요약에는 URL 이 없으므로(응답 최상위 템플릿으로 대체됨)
            # 단건 반환에는 sourceUrl 을 붙여 준다.
            kind = "question" if domain == "interpretation" else "precedent"
            document = {**item, "sourceUrl": detail_url(item["ntstDcmId"], kind)}
        else:
            document = await get_document(
                item["ntstDcmId"],
                include_full_text=include_full_text,
                body_limit=body_limit,
            )
        out: dict[str, Any] = {
            "found": True,
            "exactMatch": True,
            "domain": domain,
            "document": document,
        }
        if resolved_by_context:
            out["resolvedBy"] = "document_number_and_context"
            out["candidateCount"] = len(exact)
        return out

    if exact:
        exact_documents = [slim(item, _SIMILAR_KEYS) for _domain, item in exact.values()]
        return {
            "found": False,
            "exactMatch": False,
            "ambiguous": True,
            "normalizedDocumentNumber": parsed.canonical,
            "candidateCount": len(exact_documents),
            "candidates": exact_documents,
            "triedQueries": tried,
            "searchedDomains": searched,
            "note": (
                "동일한 문서번호를 가진 자료가 여러 건입니다. 문서 주제나 ntstDcmId로 "
                "대상을 구분하세요."
            ),
        }

    similar_documents = [slim(s, _SIMILAR_KEYS) for s in list(similar.values())[:similar_limit]]
    note = (
        "입력한 문서번호와 정확히 일치하는 문서가 없습니다. similarDocuments 는 문서번호 "
        "일부가 겹치는 별개의 문서이며, 요청한 문서가 아닙니다. 이 중 하나를 정답으로 "
        "제시하지 마세요."
        if similar_documents
        else "입력한 문서번호와 정확히 일치하는 문서도, 부분적으로 겹치는 문서도 찾지 못했습니다."
    )

    out: dict[str, Any] = {
        "found": False,
        "exactMatch": False,
        "normalizedDocumentNumber": parsed.canonical,
        "inputInterpretation": parsed.interpretation(),
        "triedQueries": tried,
        "searchedDomains": searched,
        "note": note,
    }
    if similar_documents:
        out["similarDocuments"] = similar_documents
    return out
