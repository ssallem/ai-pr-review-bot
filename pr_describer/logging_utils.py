"""표준 logging 래퍼. 패키지 전체에서 동일한 포맷 보장."""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _ensure_configured() -> None:
    """루트 로거를 한 번만 설정 — 중복 핸들러 방지."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("PR_DESCRIBER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))

    root = logging.getLogger("pr_describer")
    root.setLevel(level)
    # 자식 핸들러가 없을 때만 추가 (테스트 격리 안전).
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """모듈 단위 로거 반환. `get_logger(__name__)` 패턴으로 사용."""
    _ensure_configured()
    # `pr_describer.xxx` 형식이면 그대로, 아니면 prefix 부여하지 않음.
    return logging.getLogger(name)
