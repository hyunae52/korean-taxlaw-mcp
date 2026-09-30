# 개발 가이드

## 준비 사항

- Python 3.11 이상
- [uv](https://docs.astral.sh/uv/)

## 개발 환경

```bash
git clone https://github.com/zisu17/korean-taxlaw-mcp.git
cd korean-taxlaw-mcp
uv sync --extra dev
```

## 테스트

일반 테스트는 원본 사이트를 호출하지 않습니다.

```bash
uv run pytest -q --ignore=tests/test_live.py
```

실서버 통합 테스트는 명시적으로 활성화한 경우에만 실행됩니다.

```bash
NTS_LIVE=1 uv run pytest -q tests/test_live.py
```

## 브랜치와 릴리스

기능 변경은 `feature/<작업명> → release/v<버전> → main` 순서로 반영합니다.
기능 PR의 대상은 해당 릴리스 브랜치이며, 릴리스 PR이 최종적으로 `main`을 대상으로 합니다.
기능 PR과 릴리스 PR 모두 CI 통과를 확인한 뒤 병합합니다.

릴리스 준비 시 `pyproject.toml`과 `src/korean_taxlaw_mcp/__init__.py`의 버전을 함께
변경하고 `uv lock`으로 잠금 파일을 갱신합니다. `uv sync --locked --extra dev`,
오프라인 테스트, `uv build`를 확인해 버전이나 잠금 파일이 어긋난 상태로 병합하지 않습니다.
