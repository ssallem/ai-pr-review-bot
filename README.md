# AI PR Review Toolkit

> Claude로 한국어 PR 리뷰·설명·체인지로그를 자동화하는 CLI · GitHub Action 도구

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**라이브 데모 · 사용 가이드:** [ai-review-kit.pages.dev](https://ai-review-kit.pages.dev/)

---

## 무엇을 하나

본인 회사의 PR 코드 리뷰가 시니어 한 명에게 몰리고, 매주 5~10건씩 30분~1시간씩 끌어가나요?
이 도구는 Claude AI를 시니어 1차 리뷰어로 세워 그 시간을 **5분으로 압축**합니다.

### 실측 dogfood (local-fx v0.3.2, 1,800 LOC PR)

- 수동 시니어 리뷰 추정: 50~90분
- Claude AI 처리: 약 5분 (PR 리뷰 4분 + 설명 1분)
- 사람 검토(수용/기각): 5~15분
- **합계 10~20분 → 시간 절감 70~85%**
- 발견 가치: WARNING 5건(보안 path-traversal 포함) + SUGGESTION 8건 + 칭찬 2건
- 비용: ₩0 (Claude Max 구독) 또는 약 ₩300/회 (API)

자세한 결과는 [라이브 데모 페이지](https://ai-review-kit.pages.dev/demo) 참고.

---

## 두 가지 봇

### `pr-review` — 한국어 PR 코드 리뷰

- 입력: GitHub PR URL 또는 git diff (stdin)
- 출력: CRITICAL / WARNING / SUGGESTION 분류 + 한국어 마크다운 리뷰
- 형태: CLI + GitHub Action 템플릿

### `pr-describe` — PR 설명·체인지로그 자동 생성

- 입력: 브랜치 커밋 목록 + diff
- 출력: PR 제목(Conventional Commits) + 설명 + Keep a Changelog 형식 체인지로그
- 형태: CLI + GitHub Action 템플릿

---

## 설치 (현재는 GitHub clone)

```bash
git clone https://github.com/ssallem/ai-pr-review-bot
cd ai-pr-review-bot
pip install -e .
```

PyPI 정식 게시 후엔 `pip install ai-pr-review-toolkit` 한 줄로 가능.

### 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY 채우기
```

API 키 발급: <https://console.anthropic.com/settings/keys>

또는 본인이 Claude Max 구독 중이면 [Claude Code CLI](https://docs.claude.com/claude-code)와 함께 쓰는 별도 패턴 가능 (라이브 데모 페이지의 데모는 이 방식으로 비용 ₩0 실측).

---

## 빠른 사용 예

```bash
# 로컬 git diff → 한국어 PR 리뷰
git diff main..HEAD | pr-review --from-stdin

# GitHub PR URL → 댓글로 자동 작성
pr-review --pr https://github.com/your-org/your-repo/pull/142 --post

# 브랜치 커밋 묶음 → PR 설명 자동 생성
pr-describe --branch feature/login --base main
```

자세한 옵션은 `pr-review --help`, `pr-describe --help`.

---

## GitHub Action 자동화

`templates/pr-review.yml`을 본인 저장소의 `.github/workflows/`에 복사하면, PR 열림·동기화 시 봇이 자동으로 한국어 리뷰 댓글을 답니다.

```yaml
# .github/workflows/pr-review.yml
name: AI PR Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: |
          git clone https://github.com/ssallem/ai-pr-review-bot
          cd ai-pr-review-bot
          pip install -e .
      - run: pr-review --pr ${{ github.event.pull_request.html_url }} --post --output md
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`templates/pr-describe.yml`도 동일 패턴으로 PR 설명 자동 생성.

---

## 테스트

```bash
python -m pytest tests/ -v --cov=pr_reviewer --cov=pr_describer
```

97개 테스트 함수 (pr_reviewer 54 + pr_describer 43), API 호출 0건 (모두 mock).

---

## 기술 스택

- Python 3.11+
- [anthropic](https://github.com/anthropics/anthropic-sdk-python) SDK >= 0.40
- [click](https://click.palletsprojects.com/) — CLI
- [httpx](https://www.python-httpx.org/) — HTTP
- [pydantic](https://docs.pydantic.dev/) — 설정 검증
- pytest + pytest-asyncio + pytest-cov

---

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE) 참고.

---

## 만든 사람

**FirstNode** · <ssallem@kakao.com> · GitHub [@ssallem](https://github.com/ssallem)

- [라이브 데모 + 사용 가이드](https://ai-review-kit.pages.dev/)
- 도입 문의 또는 컨설팅: 이메일 또는 [Calendly](https://calendly.com/) (URL 별도 안내)

문제·기능 제안은 [Issues](https://github.com/ssallem/ai-pr-review-bot/issues)로.

---

## English (brief)

Korean-first PR review bot. Uses Claude API (or Claude Max + CLI subprocess) to produce
WARNING/SUGGESTION-categorized PR reviews in Korean. CLI + GitHub Action templates included.

Dogfood result on a 1,800 LOC Chrome extension PR: manual ~50-90 min → AI 5 min + human verify 5-15 min.
Time saved: 70-85%. Cost: ₩0 with Claude Max subscription.

Install: `git clone https://github.com/ssallem/ai-pr-review-bot && cd ai-pr-review-bot && pip install -e .`
