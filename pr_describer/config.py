"""환경변수 기반 설정 — Fail Fast 원칙으로 모듈 로드 시 즉시 평가."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ANTHROPIC_API_KEY, GITHUB_TOKEN, CLAUDE_MODEL을 환경변수에서 로드한다.

    - ANTHROPIC_API_KEY: 필수. Claude API 호출 키.
    - GITHUB_TOKEN: 선택. PR 메타데이터 조회/업데이트 시 필요.
    - CLAUDE_MODEL: 선택. 미설정 시 claude-sonnet-4-6 기본값.

    .env 파일이 작업 디렉토리에 있으면 자동 로드된다 (pydantic-settings).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: str = Field(
        ...,
        description="Claude API 키. https://console.anthropic.com 에서 발급.",
    )
    github_token: str | None = Field(
        default=None,
        description="GitHub PAT (선택). PR 댓글/조회 시 필요.",
    )
    claude_model: str = Field(
        default="claude-sonnet-4-6",
        description="Claude 모델 ID. 미설정 시 sonnet-4-6.",
    )


# 모듈 레벨 즉시 평가 — ANTHROPIC_API_KEY 누락 시 import 시점에 ValidationError.
# 테스트에서는 monkeypatch로 환경변수 주입 후 importlib.reload(config)로 회피한다.
settings = Settings()  # type: ignore[call-arg]
