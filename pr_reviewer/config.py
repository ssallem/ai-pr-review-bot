"""환경 변수 설정. Fail-Fast: 필수 키 누락 시 모듈 임포트 즉시 ValidationError."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """런타임 환경 변수.

    pydantic-settings가 .env 자동 로드.
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
        description="GitHub PAT. PR 조회/댓글 작성 시 필요.",
    )
    claude_model: str = Field(
        default="claude-sonnet-4-6",
        description="기본 Claude 모델 ID.",
    )


settings = Settings()
