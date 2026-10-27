"""테스트 공통 도구. 실제 Copilot을 호출하지 않고 가짜 에이전트로 실제 워크플로를 돌린다."""

from __future__ import annotations

import inspect
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from agent_framework import AgentResponse, AgentResponseUpdate, AgentSession, Content, Message, ResponseStream
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import PipelineRunner  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.secrets import Secrets  # noqa: E402
from app.storage import InMemoryRepository  # noqa: E402


def _messages_text(messages) -> str:
    if messages is None:
        return ""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, Message):
        return messages.text or ""
    return "\n".join(_messages_text(m) for m in messages)


class FakeAgent:
    """Agent Framework의 SupportsAgentRun 프로토콜을 따르는 가짜 에이전트."""

    def __init__(self, name: str, text: str, on_run=None):
        self.id = name
        self.name = name
        self.description = None
        self.text = text
        self.on_run = on_run
        self.seen_input = ""

    def create_session(self, *, session_id=None):
        return AgentSession(session_id=session_id)

    def get_session(self, service_session_id, *, session_id=None):
        return AgentSession(session_id=session_id)

    async def _before(self, messages):
        self.seen_input = _messages_text(messages)
        if self.on_run:
            result = self.on_run(self.seen_input)
            if inspect.isawaitable(result):
                await result

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        if stream:
            async def gen():
                await self._before(messages)
                half = len(self.text) // 2
                for chunk in (self.text[:half], self.text[half:]):
                    yield AgentResponseUpdate(contents=[Content.from_text(chunk)], role="assistant", author_name=self.name)

            return ResponseStream(gen(), finalizer=lambda ups: AgentResponse.from_updates(ups))

        async def done():
            await self._before(messages)
            return AgentResponse(messages=[Message("assistant", [self.text], author_name=self.name)])

        return done()


REFUND_REPLY = (
    "안녕하세요, 고객님. 주문하신 모니터 조명(ORD-1007)은 현재 배송준비 상태입니다. "
    "환불 정책 1조에 따라 발송 전에는 전액 환불을 요청할 수 있어 47,000원 환불 요청을 접수했습니다. "
    "담당자 확인 후 처리 결과를 안내드리겠습니다."
)


def writer_text(reply: str = REFUND_REPLY, needs_refund: bool = True) -> str:
    payload = {
        "reply": reply,
        "needs_refund": needs_refund,
        "refund_reason": "발송 전 환불 요청" if needs_refund else "",
        "citations": [{"sentence": reply.split(". ")[0], "evidence": ["ORDER", "REFUND-1"]}],
    }
    return reply + "\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


TRIAGE_TEXT = '환불을 요청하는 차분한 문의입니다.\n{"category": "환불", "anger": 2, "summary": "환불 요청"}'


def make_factory(writer: str | None = None, call_tools: bool = True, triage: str = TRIAGE_TEXT, log: dict | None = None):
    """가짜 에이전트 팩토리. 조사 에이전트는 실제 도구 함수를 호출한다."""
    writer = writer if writer is not None else writer_text()

    def factory(name, instructions, tools):
        if log is not None:
            log[name] = {"instructions": instructions, "tools": [t.__name__ for t in tools]}
        if name == "triage":
            return FakeAgent(name, triage)
        if name == "investigator":
            async def on_run(seen: str):
                if not call_tools:
                    return
                order = re.search(r"주문번호: (ORD-\d{4})", seen).group(1)
                by_name = {t.__name__: t for t in tools}
                await by_name["get_order"](order)
                await by_name["search_policy"]("환불", "환불")
                await by_name["get_inquiry_history"](order)

            return FakeAgent(name, "- [ORDER] 배송준비 상태\n- [REFUND-1] 발송 전 전액 환불 가능", on_run)
        return FakeAgent(name, writer)

    return factory


def make_settings(**overrides):
    base = replace(
        load_settings(),
        key_vault_url=None,
        storage_table_url=None,
        appinsights_connection_string=None,
        rate_limit_per_hour=20,
        pipeline_timeout_seconds=20,
    )
    return replace(base, **overrides)


def make_app(factory=None, **settings_overrides):
    settings = make_settings(**settings_overrides)
    repo = InMemoryRepository()
    runner = PipelineRunner(None, settings, agent_factory=factory or make_factory())
    return create_app(settings=settings, repo=repo, runner=runner, secrets=Secrets("test-token", "admin-pw"))


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
