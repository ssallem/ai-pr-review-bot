"""pr_describer 전용 conftest.

- 프로젝트 루트(sys.path) 주입: pyproject.toml에 packages가 누락된 경우에도
  `import pr_describer.*`가 동작하도록 보장.
- ANTHROPIC_API_KEY 등 환경변수가 항상 존재하도록 세션 단위 fixture로 설정.
- 각 테스트 시작 시 pr_describer.config.settings를 reload해서 격리.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True, scope="session")
def _set_env_for_session() -> Iterator[None]:
    """세션 전체에 더미 시크릿 주입."""
    prior_env = {
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
        "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN"),
        "CLAUDE_MODEL": os.environ.get("CLAUDE_MODEL"),
    }
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-dummy"
    os.environ["GITHUB_TOKEN"] = "ghp-test-dummy"
    os.environ["CLAUDE_MODEL"] = "claude-sonnet-4-6"
    try:
        yield
    finally:
        for k, v in prior_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _reload_settings() -> Iterator[None]:
    """각 테스트마다 settings를 fresh import — monkeypatch 격리 안전."""
    if "pr_describer.config" in sys.modules:
        importlib.reload(sys.modules["pr_describer.config"])
    yield
