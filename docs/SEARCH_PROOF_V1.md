# Ordinary NTS list proof

`TAXLAW_SEARCH_PROOF_V1=1` enables validation only for ordinary
`search_tax_interpretations` and `search_tax_decisions` list calls. It defaults
off. Exact-number lookup, special modules, compound search and OLTA retain their
existing behavior. There is no new request, retry, query expansion or body read.

The raw action must have `status: SUCCESS` and a data object for ASIPDI002PR01.
The payload must contain one `top` record, `categoryMap.SUB_ID_CATEGORY` as an
array and `body` as an array. The service uses **sparse category counts**, not an
explicit total: omitted classes have no hits. In particular, an empty category
array plus an empty body is the real zero-result form. Missing structure is not
equivalent to this form. Invalid counts, invalid row identities, contradictory
counts and an empty in-range page raise PARSE_ERROR.
Each returned class's unique document count must also fit its own facet count;
a row in an omitted/zero class is contradictory even when the overall sum fits.
Duplicate rows still produce partial proof rather than complete-page evidence.

`tests/fixtures/search_empty_question_v1.json.gz` and
`search_empty_precedent_v1.json.gz` were captured from the public action on
2026-11-09 with query `원본응답검증없는검색어20261109xyz`, page 1, size 2,
all ordinary classes in each collection and SCORE/DESC sorting. No login or
credentials were used. Only response correlation fields named wnSessionUuid
were removed; the success envelope, category map and body are unchanged.
The existing ten public positive fixtures cover all ordinary document classes.

The provider retains the legacy NOT_FOUND envelope for compatibility and adds
`error.detail.searchProof` on empty list results. Positive responses carry
`searchProof` alongside the unchanged items. Unknown tax filters mark the proof
partial. Consumers must bind the contract to their actual route, arguments and
reviewed provider pin; a marker from request arguments is never evidence.

`empty_confirmed` only covers the requested source and scope on page 1.
`page_out_of_range` does not establish that previous pages were read. Neither
result establishes legal absence, research completeness or sufficient evidence.
Version/contract changes require renewed consumer validation. Disable the flag
to restore the legacy parser and output; the strict cache has a separate key.
