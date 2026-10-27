"""환경변수 기반 설정. 비밀 값은 여기서 다루지 않는다 (app/secrets.py 참고)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    key_vault_url: str | None
    storage_table_url: str | None
    appinsights_connection_string: str | None
    copilot_model: str | None
    agent_timeout_seconds: int
    pipeline_timeout_seconds: int
    max_concurrent_runs: int
    rate_limit_per_hour: int
    retention_hours: int
    copilot_work_dir: str

    @property
    def uses_azure(self) -> bool:
        return bool(self.key_vault_url and self.storage_table_url)


def load_settings() -> Settings:
    return Settings(
        key_vault_url=os.getenv("KEY_VAULT_URL") or None,
        storage_table_url=os.getenv("STORAGE_TABLE_URL") or None,
        appinsights_connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or None,
        copilot_model=os.getenv("COPILOT_MODEL") or None,
        agent_timeout_seconds=_int("AGENT_TIMEOUT_SECONDS", 90),
        pipeline_timeout_seconds=_int("PIPELINE_TIMEOUT_SECONDS", 180),
        max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 2),
        rate_limit_per_hour=_int("RATE_LIMIT_PER_HOUR", 8),
        retention_hours=_int("RETENTION_HOURS", 24),
        copilot_work_dir=os.getenv("COPILOT_WORK_DIR", "/tmp/copilot-work"),
    )


# 기획서(PRD) 4장의 실측값: 운영자가 앱 없이 처리한 시간(초)
MANUAL_BASELINE_SECONDS = {
    "배송": 4 * 60 + 20,
    "환불": 5 * 60 + 14,
    "교환": 4 * 60 + 45,
}
MANUAL_BASELINE_AVERAGE = sum(MANUAL_BASELINE_SECONDS.values()) / len(MANUAL_BASELINE_SECONDS)
