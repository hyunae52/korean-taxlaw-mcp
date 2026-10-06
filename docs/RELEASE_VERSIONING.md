# TaxLab 세법 MCP 버전 관리

## `post1`을 붙인 이유

`2.1.0`은 원작자 버전이고, `2.1.0.post1`은 TaxLab이 구분해서 기록한 후속 배포본이다.
2026-09-25에 원본과 포크의 설치 경로·배포 식별을 구분하면서 이 방식을 도입했다.
현재 `2.1.0.post1`은 원작자 `2.1.0`의 조회 코드를 모두 포함한다. 런타임 소스의 차이는
`__version__` 문자열뿐이다. `post1` 자체가 추가 조회 기능이나 법률 판단 개선을 뜻하지 않는다.

Python의 `.postN`은 일반적인 후속 배포 표기이며, 본래 포크를 뜻하는 예약어는 아니다.
실제 코드 패치는 별도 버전으로 검토한다. `.post`만 무조건 올리는 자동 규칙은 사용하지 않는다.
[Python 버전 명세](https://packaging.python.org/en/latest/specifications/version-specifiers/#post-releases)를 따른다.

이미 설치·태그·운영 pin에 사용한 `2.1.0.post1`을 `2.1.0`으로 소급해서 바꾸지 않는다.
앞으로도 원작자 기준, 우리 배포 버전, 실제 운영 커밋을 따로 기록한다.

## 현재 대응 관계

| 구분 | 값 |
|---|---|
| 원작자 | `zisu17/korean-taxlaw-mcp` |
| 원작자 기준 | `2.1.0` / `a91872fed2c12cd51fffdc4c2dbbfcabe997262b` |
| 검토 포크 | `hyunae52/korean-taxlaw-mcp` |
| 우리 배포 버전 | `2.1.0.post1` |
| 고정 릴리스 태그 | `taxlab-v2.1.0.post1` |
| 해당 릴리스 커밋 | `16a08b9af70f49fc60a5f54ab0f9812be206c8f5` |

기계가 읽는 기준은 [`.github/taxlab-release.json`](../.github/taxlab-release.json)이다.
공개 배포 이력은 [GitHub Releases](https://github.com/hyunae52/korean-taxlaw-mcp/releases)에 남긴다.
현재 운영 커밋은 Legal Harness의 `upstreams/korean-taxlaw-mcp.json` 및
운영 `/health.taxlaw_release`로 확인한다. 포크 main에 문서·CI 변경이 추가됐다고
운영 패키지를 다시 설치하거나 배포 번호를 올리지 않는다.

## 법령 MCP와 함께 관리하는 절차

1. GCE의 매일 03:30 한국시간 감시에서 법령 MCP 최신 npm, 세법 원작자와 포크의 변경을 확인한다.
2. 세법 포크의 매일 04:10 한국시간 예약 워크플로가 원본을 병합한 후보를 만들고
   Python 3.11/3.13 오프라인 시험 후 PR을 생성한다. GitHub 예약 실행 시각은 지연될 수 있다.
3. 변경 검토 시 원본 버전·커밋과 우리 배포 버전을 위 JSON에 기록하고,
   `pyproject.toml`, `src/korean_taxlaw_mcp/__init__.py`, `uv.lock`의 버전을 함께 맞춘다.
4. 기존 `Tests`의 버전 일치 검사와 오프라인 회귀 시험이 통과한 정확한 커밋을 검토한다.
   병합·릴리스 전에 전체 이력과 태그를 받은 체크아웃에서 아래 전체 검사를 별도로 실행한다.
   후보 자동 생성과 운영 가능한 릴리스 검증은 구분한다.
5. 검증한 커밋에 새 `taxlab-v<버전>` 태그와 릴리스 이력을 남긴다. 기존 태그는 덮어쓰지 않는다.
6. 실행 코드가 바뀌었다면 Legal Harness의 커밋·아카이브 해시·버전·도구 스키마를
   별도 PR에서 고정한다. 새 버전 형식도 설치기·감시기·배포기 호환성을 함께 확인한다.
7. 분리된 후보 설치와 실제 조회 검증 후 운영에 적용하고, 공개 health·MCP 조회·일일 감시 결과를 확인한다.

2026-10-07부터 위 절차는 [예약 동기화](FORK_MAINTENANCE.md)가 검사 후 자동 수행한다.
테스트·충돌 검사를 통과해야 병합·릴리스하며, 운영 서버는 별도 통합·실제 조회 검증 후 전환한다.
원작자 업데이트가 없으면 이미 검증한 버전을 유지한다.

## 버전 검사

```sh
# 기존 Tests CI에도 포함되는 버전 선언 검사와 회귀 시험
python .github/scripts/check_release_version.py --metadata-only
python -m unittest discover -s tests -p 'test_release_version.py' -v

# 릴리스 전 필수 검사: 원본 커밋과 태그를 포함한 전체 이력 필요
# shallow clone이라면 먼저 git fetch --unshallow origin을 실행한다.
git fetch origin --tags
python .github/scripts/check_release_version.py
```

검사 스크립트는 외부 API 호출이나 MCP 패키지 import 없이 다음을 확인한다.

- JSON, 패키지 메타데이터, 런타임 버전, 잠금 파일의 자체 패키지 버전이 일치하는가.
- 원본 기준 커밋을 실제로 포함하며 그 커밋의 원본 버전이 기록과 같은가.
- 태그 이름과 배포 버전이 일치하는가.
- 이미 존재하는 태그와 비교해 `src`, 패키지 설정, 잠금 파일을 바꾸면서 같은 버전을 재사용하지 않았는가.

기존 CI는 shallow checkout을 사용하므로 실제 체크아웃의 **버전 선언 일치**와
검사기 자체의 회귀 시험을 자동 실행한다. 원본 포함 여부와 기존 태그 대비 코드 비교는
전체 검사에서 확인한다. `--metadata-only` 결과는 `verification_scope: metadata_only`로
명시하며, 전체 검사 성공이나 릴리스 승인을 대신하지 않는다. 전체 검사는 shallow 이력을 거부한다.

이 검사는 버전·출처 대응을 확인한다. 법률 판단의 정확성이나 운영 적용 완료를 인증하지 않는다.
