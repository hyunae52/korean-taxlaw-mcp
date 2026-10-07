# 사용 가이드

## 국세와 지방세 구분

두 출처는 문서 체계와 근거 유형이 다르므로 별도 도구로 조회합니다.

| 구분 | 대표 세목 | 문서번호 예 |
|---|---|---|
| 국세 | 양도소득세, 법인세, 부가가치세, 소득세, 상속·증여세 | `서면-2026-법규재산-0119` |
| 지방세 | 취득세, 등록면허세, 재산세, 자동차세, 주민세, 지방소득세 | `부동산세제과-1794(2026.6.9.)호` |

## 도구 선택

| 하고 싶은 일 | 도구 |
|---|---|
| 국세 문서번호로 정확히 찾기 | `lookup_tax_document` |
| 국세청 세법해석례 검색 | `search_tax_interpretations` |
| 국세 판례·결정례 검색 | `search_tax_decisions` |
| 검색 결과에서 선택한 국세 문서 본문 조회 | `get_tax_document` |
| 기본통칙·집행기준·고시·훈령 검색 | `search_tax_guidance` |
| 기본통칙·집행기준의 특정 조항 조회 | `get_tax_guidance` |
| 법령 서식·별표 검색 | `search_tax_forms` |
| 국세 전체 영역 통합 검색 | `search_taxlaw` |
| 질문과 관련된 근거를 층위별로 수집 | `tax_research` |
| 지방세 유권해석 검색 | `search_local_tax_interpretations` |
| 지방세 심판·감사·법원·헌재 결정례 검색 | `search_local_tax_decisions` |
| 지방세 문서번호로 본문 조회 | `lookup_local_tax_document` |

문서번호를 알고 있다면 `lookup_tax_document` 또는 `lookup_local_tax_document`부터
사용하세요. 키워드 검색보다 빠르고 정확합니다.

## 문서번호 조회 원칙

띄어쓰기와 구분자 등 표기 차이는 정규화하지만, 일부만 일치하는 문서를 정답으로 반환하지 않습니다.

```text
정확히 일치        → found: true, exactMatch: true
같은 번호가 여러 건 → AMBIGUOUS_DOCUMENT_NUMBER
일치하는 문서 없음 → NOT_FOUND
비슷한 문서        → similarDocuments에만 표시
```

예를 들어 `법규재산-0119`처럼 일부만 입력하면 유사 문서를 안내할 수는 있지만 요청한 문서로
단정하지 않습니다. 구형 문서번호가 여러 문서에 재사용된 경우에는 `context_query`에 문서
주제의 핵심어를 넣거나, 후보의 `ntstDcmId`를 `get_tax_document`에 전달해 대상을 지정합니다.
최종 응답의 문서번호는 원본 표기를 따릅니다.

`search_taxlaw`도 문서번호가 중복되면 같은 오류와 후보 목록을 반환합니다.
`법인46012-1784 퇴직금`처럼 번호 뒤에 주제를 붙여 구분할 수 있습니다.
유일성 확인은 다음 페이지까지 진행합니다. 검색어별 최대 10페이지(번호 검색 300건,
문맥 검색 1,000건)를 넘으면 `LOOKUP_INCOMPLETE`로 알리며 정답이나 부존재를 확정하지 않습니다.
이때는 검색 도구에서 후보를 확인한 뒤 `get_tax_document`에 `ntstDcmId`를 전달하세요.

## 별도 자료 유형 검색

- `search_tax_interpretations(type="curated_issue", query="상속")`: 자주찾는 쟁점별 사례
- `search_tax_decisions(type="audit_appeal", query="2025심사2038")`: 감사원 심사청구
- `search_tax_decisions(type="taxpayer_protection", query="세무조사")`: 납세자보호위원회 심의사례

`search_taxlaw`에서도 `감사원 심사청구 법인세`, `납세자보호위원회 세무조사`,
`쟁점별 사례 상속`처럼 자료 유형을 명시하면 해당 전용 검색을 사용합니다.
여러 자료 유형을 함께 지정하면 결과를 유형별로 나누고 각 유형에 `limit_per_domain`을
적용합니다. 특정 출처만 실패하면 나머지 결과와 `partialErrors`를 함께 반환합니다.
유형을 명시하지 않은 통합검색과 기존 `type="all"`의 범위는 유지됩니다.

쟁점별 사례는 일반 텍스트와 단일 세목, 감사원·납세자보호위원회는 일반 텍스트와 날짜를
지원합니다. 지원하지 않는 필터와 잘못된 날짜는 `INVALID_INPUT`으로 반환합니다.
감사원 본문은 첨부 파일로만 제공되며 `attachment_status=true`는 파일 앞부분의 서명만
확인합니다. 전체 파일 검증이나 본문 추출을 의미하지 않습니다.

## 감사원 PDF 본문 조회

검색에서 확인한 감사원 결정번호를 본문 조회에 전달합니다. 도구를 새로 추가하지 않고
기존 `lookup_tax_document`와 `get_tax_document`를 사용합니다.

```python
lookup_tax_document(document_number="2024심사636")
get_tax_document(document_number="2024-심사-636", page_start=2, page_end=4)
```

- 원본 검색에서 결정번호가 정확하고 유일하게 일치하는 경우에만 그 행의 첨부를 읽습니다.
  감사원 번호는 연도 다음에 `심사` 또는 `감심`이 오는 형식입니다. 다른 번호 체계의 기존
  상세 조회 경로는 유지합니다. 감사원 조회는 `context_query`를 지원하지 않습니다.
- `fullText`는 PDF 텍스트 레이어의 추출 결과입니다. `pages`의 `pageNumber`는 실제 PDF
  페이지 번호(1부터)이며, 인쇄된 문서 쪽수와 다를 수 있습니다. 각 페이지의 `sourceUrl`에
  `#page=N`이 붙습니다. `start`(포함)·`end`(제외)는 `fullText` 내 **유니코드 코드 포인트**
  위치입니다. JavaScript의 UTF-16 인덱스와는 보충 문자에서 차이가 납니다.
- `attachment.sha256`, `attachment.downloadedBytes`, `retrievedAt`로 어떤 파일을 언제
  읽었는지 확인합니다. 해시는 다운로드한 바이트의 식별값이지 법적 진위 검증이 아닙니다.
- 한 번에 최대 20페이지, 기본 30,000자(`body_limit`으로 최대 200,000자)를 반환합니다.
  `nextPage`가 있으면 그 페이지부터 다시 요청합니다. 한 페이지 자체가 글자 제한을 넘으면
  그 페이지가 잘렸다고 표시하고 `nextPage`에 같은 페이지를 줍니다. 이 경우 그 페이지만
  더 큰 `body_limit`으로 조회하세요. `page_end`를 생략하면 시작점부터 최대 20페이지입니다.
- `bodyPartial=true`는 일부 페이지만 요청했거나, 글자 제한 또는 읽지 못한 페이지가 있음을
  뜻합니다. `fullTextTruncated`는 글자 제한 여부이고 `pagesWithoutText`는 텍스트를 얻지
  못한 페이지입니다. 일부 페이지만 읽고 결정문 전체를 확인했다고 표현하지 마세요.
- `bodyPartial=false`여도 이미지 속 글자·표 읽기 순서·인코딩 정확성이 확인된 것은 아닙니다.
  `completeness`는 항상 `unverified`입니다. 필요한 표와 원문 표현은 PDF를 대조하세요.
- `include_full_text=false` 또는 `detail="compact"`는 PDF를 다운로드하지 않고 메타데이터만
  반환합니다. 이 경우 페이지 범위를 지정할 수 없습니다.
- 파서는 별도 프로세스에서 실행합니다. 입출력은 쓰기 가능한 시스템 임시 폴더의 임시
  파일을 사용하고 작업 종료 시 닫아 삭제합니다. 취소·시간 초과 시에도 파서를 종료하며,
  종료 확인에는 별도 5초 상한을 둡니다. 임시 저장소를 사용할 수 없으면 추출 오류를 반환합니다.
- 스캔본처럼 텍스트를 전혀 얻지 못한 경우, 암호화 PDF, HWP/HWPX는
  `DETAIL_NOT_AVAILABLE`과 원문 주소·사유를 반환합니다. PDF 대신 오류 HTML이 내려오면
  `UPSTREAM_ERROR`, 손상된 PDF는 `PARSE_ERROR`입니다. 어느 것도 자료 부존재를 뜻하지 않습니다.

다운로드는 8 MiB, PDF 전체는 200페이지로 제한합니다. 파서는 별도 프로세스에서 실행되고
15초가 지나거나 요청이 취소되면 종료됩니다. 동시에 최대 두 건만 PDF를 처리합니다.
Linux에서는 추가로 프로세스 주소 공간 384 MiB·CPU 10초를 제한합니다.
기존 검색과 첨부의 4 KiB 확인은 본문을 자동 다운로드하지 않습니다.

## 근거 유형

| 값 | 의미 |
|---|---|
| `nts_ruling` | 국세청 해석례·예규 |
| `local_ruling` | 행정안전부·법제처 지방세 유권해석 |
| `nts_guidance` | 기본통칙·집행기준·고시·훈령 |
| `adjudication` | 과세적부·이의신청·심사청구·심판청구·감사원 결정 |
| `court_case` | 법원 판례·헌법재판소 결정 |

해석례와 행정 기준은 법령 그 자체가 아니며 법원을 구속하지 않습니다.

## 오류 해석

자료가 실제로 없는 경우와 원본 서버 문제로 조회하지 못한 경우를 구분합니다.

| 오류 | 의미 |
|---|---|
| `NOT_FOUND` | 원본에 일치하는 자료가 없음 |
| `AMBIGUOUS_DOCUMENT_NUMBER` | 같은 문서번호가 여러 건이라 하나를 확정할 수 없음 |
| `LOOKUP_INCOMPLETE` | 후보 검색 한도에 도달해 문서번호의 유일성·부존재를 확정하지 못함 |
| `DETAIL_NOT_AVAILABLE` | 문서는 있지만 본문을 확보하지 못함(원본 미제공·추출 미지원·처리 상한 등) |
| `UPSTREAM_ERROR` | 원본 사이트 오류·점검·비정상 응답 |
| `RATE_LIMITED` | 요청 보호 한도 초과 또는 냉각 상태 |
| `TIMEOUT` | 원본 사이트 응답 시간 초과 |
| `PARSE_ERROR` | 원본 응답 구조 변경 등으로 해석 실패 |
| `INVALID_INPUT` | 입력값 오류 |

원본 서버 장애를 자료 부존재로 바꿔 답하지 않습니다. 오류 응답에는 확인되지 않은 본문이나
결론을 생성하지 않도록 `guardrail` 정보도 포함됩니다.

## 지원 범위의 한계

- 법률·시행령·시행규칙 본문은 국가법령정보센터 기반의
  [chrisryugj/korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp)에서 조회할 수 있습니다.
- 일반 판례 전체가 아니라 원본 시스템에서 조세 자료로 분류한 문서를 조회합니다.
- 원본이 본문을 제공하지 않는 세법집행기준, 일부 고시·훈령, 서식 파일은 메타데이터나
  파일 식별자만 반환합니다.
- 상세한 지원·미지원 범위는 [지원 범위 조사](INVESTIGATION.md)를 참고하세요.

이 서버는 원문 검색과 구조화를 위한 데이터 접근 계층이며 세무 자문을 제공하지 않습니다.
해석례·결정례는 사건의 사실관계에 따라 결론이 달라질 수 있으므로 원문과 현행 법령을 함께
확인하세요.
