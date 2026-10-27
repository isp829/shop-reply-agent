"""비밀 값 로딩.

배포 환경: App Service Managed Identity로 Key Vault에서 읽는다 (연결 문자열·키를 쓰지 않음).
로컬 개발: KEY_VAULT_URL이 없으면 환경변수 GITHUB_TOKEN / ADMIN_PASSWORD를 쓴다.
비밀 값은 로그에 남기지 않는다.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from .config import Settings

logger = logging.getLogger(__name__)

GITHUB_TOKEN_SECRET = "GITHUB-TOKEN"
ADMIN_PASSWORD_SECRET = "ADMIN-PASSWORD"


@dataclass
class Secrets:
    github_token: str | None
    admin_password: str | None

    @property
    def ready(self) -> bool:
        return bool(self.github_token)


def load_secrets(settings: Settings, attempts: int = 3) -> Secrets:
    if not settings.key_vault_url:
        logger.info("KEY_VAULT_URL이 없어 환경변수에서 비밀 값을 읽습니다 (로컬 개발 모드)")
        return Secrets(os.getenv("GITHUB_TOKEN"), os.getenv("ADMIN_PASSWORD"))

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.key_vault_url, credential=DefaultAzureCredential())
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            token = client.get_secret(GITHUB_TOKEN_SECRET).value
            try:
                admin = client.get_secret(ADMIN_PASSWORD_SECRET).value
            except Exception:  # 관리자 비밀번호가 없어도 핵심 기능은 동작한다
                logger.warning("Key Vault에 %s가 없어 관리자 기능을 비활성화합니다", ADMIN_PASSWORD_SECRET)
                admin = None
            logger.info("Key Vault에서 비밀 값을 불러왔습니다")
            return Secrets(token, admin)
        except Exception as exc:  # 권한 전파 지연 등 일시적 오류에 대비해 재시도
            last_error = exc
            logger.warning("Key Vault 조회 실패 (%s/%s): %s", attempt, attempts, type(exc).__name__)
            time.sleep(2 * attempt)
    logger.error("Key Vault에서 비밀 값을 읽지 못했습니다: %s", type(last_error).__name__)
    return Secrets(None, None)
