"""공통 fixture. 환경 변수 더미 주입으로 settings 임포트가 실패하지 않게 보호."""

from __future__ import annotations

import os

# pydantic-settings는 모듈 임포트 시점에 평가되므로 collection 직전에 주입.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
os.environ.setdefault("CLAUDE_MODEL", "claude-sonnet-4-6")

import pytest


@pytest.fixture
def sample_diff_text() -> str:
    return (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,3 +1,5 @@\n"
        " def foo():\n"
        "-    return 1\n"
        "+    x = None\n"
        "+    return x.value  # 잠재 None 참조\n"
        "+\n"
        "+# end\n"
        "diff --git a/src/bar.js b/src/bar.js\n"
        "index 3333333..4444444 100644\n"
        "--- a/src/bar.js\n"
        "+++ b/src/bar.js\n"
        "@@ -10,2 +10,3 @@\n"
        " function bar() {\n"
        "+  console.log('debug');\n"
        " }\n"
    )


@pytest.fixture
def sample_review_json() -> str:
    return (
        '{"issues": ['
        '{"severity": "critical", "file": "src/foo.py", "line": 4, '
        '"category": "potential_bug", "message": "x가 None일 가능성", '
        '"suggested_fix": "if x is None: return None"},'
        '{"severity": "suggestion", "file": "src/bar.js", "line": 11, '
        '"category": "style", "message": "디버그 로그 제거 필요"}'
        '], "summary": "한 건의 critical 버그와 한 건의 스타일 제안 발견."}'
    )


@pytest.fixture
def github_token() -> str:
    return "ghp_test_dummy_token"
