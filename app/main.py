"""웹 앱: 라우트, 세션 분리, 보안 헤더, 스트리밍(SSE)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import grounding, metrics
from .agents import KST, CopilotClientHolder, PipelineError, PipelineRunner
from .config import Settings, load_settings
from .pii import mask_name, mask_phone
from .secrets import Secrets, load_secrets
from .storage import InMemoryRepository, TableRepository, parse_iso, utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("azure", "azure.core", "azure.identity", "azure.monitor", "opentelemetry"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ORDER_PATTERN = re.compile(r"^ORD-\d{4}$")
MAX_INQUIRY_CHARS = 2000
SESSION_COOKIE = "sid"
SESSION_MAX_AGE = 24 * 3600
STATUS_FILTERS = ("전체", "초안", "확정", "환불 대기", "환불완료", "반려")
EXAMPLES = [
    {"order": "ORD-1003", "text": "배송이 언제 오나요? 일주일째 소식이 없네요", "label": "배송 지연"},
    {"order": "ORD-1007", "text": "환불해 주세요", "label": "환불 요청"},
    {"order": "ORD-1012", "text": "다른 색상으로 교환하고 싶어요", "label": "교환 문의"},
]
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass
class AppContext:
    settings: Settings
    secrets: Secrets | None = None
    repo: Any = None
    runner: PipelineRunner | None = None
    storage_ready: bool = False
    last_purge: float = 0.0
    attempts: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))


# ---------------------------------------------------------------- 화면 도우미
def fmt_time(value: str | None) -> str:
    dt = parse_iso(value)
    return dt.astimezone(KST).strftime("%m-%d %H:%M") if dt else "-"


templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.filters["kst"] = fmt_time
templates.env.filters["seconds"] = metrics.format_seconds
templates.env.filters["mask_name"] = mask_name
templates.env.filters["mask_phone"] = mask_phone


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _sid(request: Request) -> str:
    return request.state.sid


async def _repo(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(getattr(_ctx(request).repo, method), *args, **kwargs)


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    context.setdefault("active", "")
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


# ---------------------------------------------------------------- 입력 검증
class InputError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def validate_input(request: Request, order_number: str, inquiry: str) -> tuple[str, str]:
    ctx = _ctx(request)
    order_number = (order_number or "").strip().upper()
    inquiry = (inquiry or "").strip()
    if not ORDER_PATTERN.match(order_number) or await _repo(request, "get_order", _sid(request), order_number) is None:
        raise InputError("주문번호를 확인해 주세요. 샘플 주문번호는 ORD-1001 ~ ORD-1020입니다.")
    if not inquiry:
        raise InputError("문의 내용을 입력해 주세요.")
    if len(inquiry) > MAX_INQUIRY_CHARS:
        raise InputError(f"문의 내용은 {MAX_INQUIRY_CHARS}자 이내로 입력해 주세요.")
    if ctx.runner is None or not ctx.runner.available:
        raise InputError("AI 연결 설정이 아직 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.", 503)
    window = ctx.attempts[_sid(request)]
    now = time.monotonic()
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= ctx.settings.rate_limit_per_hour:
        raise InputError(f"공개 데모는 한 시간에 최대 {ctx.settings.rate_limit_per_hour}건까지 시험할 수 있습니다.", 429)
    window.append(now)
    return order_number, inquiry


async def save_result(request: Request, order_number: str, data: dict[str, Any]) -> str:
    sid = _sid(request)
    inquiry_id = uuid.uuid4().hex[:12]
    refund_status = "환불 대기" if data.get("needs_refund") else "없음"
    record = {
        "id": inquiry_id,
        "session_id": sid,
        "order_number": order_number,
        "inquiry_masked": data.get("masked_text", ""),
        "category": data.get("category", "기타"),
        "anger": data.get("anger", 3),
        "summary": data.get("summary", ""),
        "reply_draft": data.get("reply", ""),
        "reply_final": "",
        "needs_refund": bool(data.get("needs_refund")),
        "refund_reason": data.get("refund_reason", ""),
        "refund_status": refund_status,
        "status": "초안",
        "citations": data.get("citations", []),
        "checks": data.get("checks", []),
        "evidence": data.get("evidence", []),
        "screening": data.get("screening", {}),
        "investigation": data.get("investigation", ""),
        "ai_seconds": data.get("ai_seconds"),
        "created_at": utcnow().isoformat(),
        "decided_at": None,
        "handling_seconds": None,
    }
    await _repo(request, "save_inquiry", record)
    if refund_status == "환불 대기":
        await _repo(request, "set_refund_status", sid, order_number, "환불 대기")
    return inquiry_id


def _decision_fields(record: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    created = parse_iso(record.get("created_at")) or now
    fields: dict[str, Any] = {"decided_at": now.isoformat()}
    if record.get("handling_seconds") is None:
        # 문의를 보낸 순간부터 결정까지: 초안 생성 후 경과 시간 + AI 처리 시간
        fields["handling_seconds"] = round((now - created).total_seconds() + float(record.get("ai_seconds") or 0), 1)
    return fields


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- 앱 생성
def create_app(
    settings: Settings | None = None,
    repo: Any = None,
    runner: PipelineRunner | None = None,
    secrets: Secrets | None = None,
) -> FastAPI:
    ctx = AppContext(settings=settings or load_settings(), secrets=secrets, repo=repo, runner=runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = ctx.settings
        if s.appinsights_connection_string:
            try:
                from azure.monitor.opentelemetry import configure_azure_monitor

                configure_azure_monitor(connection_string=s.appinsights_connection_string)
                logger.info("Application Insights 연결")
            except Exception:
                logger.exception("Application Insights 설정 실패")
        if ctx.secrets is None:
            ctx.secrets = await asyncio.to_thread(load_secrets, s)
        if ctx.repo is None:
            ctx.repo = TableRepository(s.storage_table_url) if s.storage_table_url else InMemoryRepository()
        if ctx.runner is None:
            ctx.runner = PipelineRunner(CopilotClientHolder(ctx.secrets.github_token, s.copilot_work_dir), s)
        try:
            await asyncio.to_thread(ctx.repo.init)
            removed = await asyncio.to_thread(ctx.repo.purge_older_than, s.retention_hours)
            ctx.last_purge = time.monotonic()
            ctx.storage_ready = True
            logger.info("저장소 준비 완료 (만료 문의 %s건 정리)", removed)
        except Exception:
            logger.exception("저장소 초기화 실패")
        yield
        if ctx.runner is not None:
            await ctx.runner.close()

    app = FastAPI(title="쇼핑몰 문의 응대 에이전트", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.middleware("http")
    async def session_and_headers(request: Request, call_next):
        sid = request.cookies.get(SESSION_COOKIE)
        is_new = not (sid and re.fullmatch(r"[0-9a-f]{32}", sid))
        if is_new:
            sid = uuid.uuid4().hex
        request.state.sid = sid
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if is_new:
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=secure)
        return response

    async def maybe_purge(request: Request) -> None:
        if not ctx.storage_ready or time.monotonic() - ctx.last_purge < 600:
            return
        ctx.last_purge = time.monotonic()
        try:
            await asyncio.to_thread(ctx.repo.purge_older_than, ctx.settings.retention_hours)
        except Exception:
            logger.exception("만료 문의 정리 실패")

    async def index_page(request: Request, status_code: int = 200, error: str | None = None, form: dict | None = None) -> HTMLResponse:
        recent: list[dict[str, Any]] = []
        try:
            recent = (await _repo(request, "list_inquiries", _sid(request)))[:3]
        except Exception:
            logger.exception("최근 문의 조회 실패")
        return render(
            request, "index.html", status_code=status_code, active="new", examples=EXAMPLES, recent=recent,
            error=error, form=form or {}, limit=ctx.settings.rate_limit_per_hour, retention=ctx.settings.retention_hours,
        )

    # ------------------------------------------------------------ 페이지
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        await maybe_purge(request)
        return await index_page(request)

    @app.get("/api/orders/{order_number}")
    async def order_preview(request: Request, order_number: str):
        order_number = order_number.strip().upper()
        if not ORDER_PATTERN.match(order_number):
            return JSONResponse({"error": "주문번호 형식이 아닙니다"}, status_code=404)
        order = await _repo(request, "get_order", _sid(request), order_number)
        if order is None:
            return JSONResponse({"error": "주문을 찾을 수 없습니다"}, status_code=404)
        return {
            "order_number": order["order_number"],
            "customer_name": mask_name(order["customer_name"]),
            "product": f"{order['product_name']} ({order['option']})",
            "amount": f"{order['amount']:,}원",
            "order_date": order["order_date"],
            "shipping_status": order["shipping_status"],
            "delivered_date": order.get("delivered_date"),
            "refund_status": order["refund_status"],
        }

    @app.post("/api/inquiries/stream")
    async def inquiry_stream(request: Request, order_number: str = Form(""), inquiry: str = Form("")):
        try:
            order_number, inquiry = await validate_input(request, order_number, inquiry)
        except InputError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status_code)
        sid = _sid(request)

        async def events():
            yield sse({"type": "start"})
            try:
                async for event in ctx.runner.stream(ctx.repo, sid, order_number, inquiry):
                    if event["type"] == "result":
                        inquiry_id = await save_result(request, order_number, event["data"])
                        yield sse({"type": "done", "url": f"/inquiries/{inquiry_id}"})
                        return
                    yield sse(event)
            except Exception:
                logger.exception("스트리밍 처리 실패")
                yield sse({"type": "error", "message": "처리 중 오류가 발생했습니다. 다시 시도해 주세요"})

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/inquiries")
    async def create_inquiry(request: Request, order_number: str = Form(""), inquiry: str = Form("")):
        form = {"order_number": order_number, "inquiry": inquiry}
        try:
            order_number, inquiry = await validate_input(request, order_number, inquiry)
            data = await ctx.runner.run(ctx.repo, _sid(request), order_number, inquiry)
        except InputError as exc:
            return await index_page(request, exc.status_code, exc.message, form)
        except PipelineError as exc:
            return await index_page(request, 502, str(exc), form)
        inquiry_id = await save_result(request, order_number, data)
        return RedirectResponse(f"/inquiries/{inquiry_id}", status_code=303)

    async def load_inquiry(request: Request, inquiry_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{12}", inquiry_id):
            return None
        return await _repo(request, "get_inquiry", _sid(request), inquiry_id)

    def not_found(request: Request) -> HTMLResponse:
        return render(request, "message.html", status_code=404, title="문의를 찾을 수 없습니다",
                      message="다른 브라우저에서 만든 문의는 볼 수 없고, 만든 지 24시간이 지난 문의는 자동으로 지워집니다.")

    async def detail_page(request: Request, record: dict[str, Any], status_code: int = 200, error: str | None = None) -> HTMLResponse:
        order = await _repo(request, "get_order", _sid(request), record["order_number"])
        counts = grounding.summary(record.get("checks") or [])
        return render(request, "inquiry.html", status_code=status_code, active="history", item=record, order=order,
                      counts=counts, error=error)

    @app.get("/inquiries/{inquiry_id}", response_class=HTMLResponse)
    async def inquiry_detail(request: Request, inquiry_id: str):
        record = await load_inquiry(request, inquiry_id)
        if record is None:
            return not_found(request)
        return await detail_page(request, record)

    @app.post("/inquiries/{inquiry_id}/confirm")
    async def confirm(request: Request, inquiry_id: str, reply: str = Form(""), acknowledged: str = Form("")):
        record = await load_inquiry(request, inquiry_id)
        if record is None:
            return not_found(request)
        reply = reply.strip()
        if not reply or len(reply) > 4000:
            return await detail_page(request, record, 400, "답장 내용을 확인해 주세요.")
        evidence_texts = [e.get("text", "") for e in record.get("evidence") or []]
        checks = grounding.verify(reply, evidence_texts, refund_approved=record.get("refund_status") == "환불완료")
        record["checks"] = checks
        record["reply_draft"] = reply
        if any(c["status"] == "unsupported" for c in checks) and not acknowledged:
            await _repo(request, "update_inquiry", _sid(request), inquiry_id, checks=checks, reply_draft=reply)
            return await detail_page(request, record, 400, "근거 없음으로 표시된 문장을 확인한 뒤 체크하고 확정해 주세요.")
        fields = {"reply_final": reply, "reply_draft": reply, "checks": checks, "status": "확정", **_decision_fields(record)}
        await _repo(request, "update_inquiry", _sid(request), inquiry_id, **fields)
        return RedirectResponse(f"/inquiries/{inquiry_id}", status_code=303)

    async def decide_refund(request: Request, inquiry_id: str, approve: bool):
        record = await load_inquiry(request, inquiry_id)
        if record is None:
            return not_found(request)
        if record.get("refund_status") != "환불 대기":
            return await detail_page(request, record, 409, "환불 대기 상태인 문의만 승인하거나 반려할 수 있습니다.")
        status = "환불완료" if approve else "반려"
        await _repo(request, "update_inquiry", _sid(request), inquiry_id, refund_status=status, **_decision_fields(record))
        await _repo(request, "set_refund_status", _sid(request), record["order_number"], status if approve else "없음")
        return RedirectResponse(f"/inquiries/{inquiry_id}", status_code=303)

    @app.post("/inquiries/{inquiry_id}/refund/approve")
    async def refund_approve(request: Request, inquiry_id: str):
        return await decide_refund(request, inquiry_id, True)

    @app.post("/inquiries/{inquiry_id}/refund/reject")
    async def refund_reject(request: Request, inquiry_id: str):
        return await decide_refund(request, inquiry_id, False)

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request, status: str = "전체"):
        if status not in STATUS_FILTERS:
            status = "전체"
        items = await _repo(request, "list_inquiries", _sid(request))
        if status != "전체":
            items = [i for i in items if i.get("status") == status or i.get("refund_status") == status]
        items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
        items.sort(key=lambda i: i.get("anger") or 0, reverse=True)
        return render(request, "history.html", active="history", items=items, status=status, filters=STATUS_FILTERS)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        items = await _repo(request, "list_inquiries", _sid(request))
        return render(request, "dashboard.html", active="dashboard", summary=metrics.summarize(items))

    @app.get("/about", response_class=HTMLResponse)
    async def about(request: Request):
        return render(request, "about.html", active="about", retention=ctx.settings.retention_hours,
                      limit=ctx.settings.rate_limit_per_hour, model=ctx.settings.copilot_model or "기본 모델")

    @app.post("/demo/reset")
    async def demo_reset(request: Request):
        await _repo(request, "reset_session", _sid(request))
        return RedirectResponse("/", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        enabled = bool(ctx.secrets and ctx.secrets.admin_password)
        return render(request, "admin.html", enabled=enabled, message=None, error=None)

    @app.post("/admin", response_class=HTMLResponse)
    async def admin_purge(request: Request, password: str = Form("")):
        expected = ctx.secrets.admin_password if ctx.secrets else None
        if not expected:
            return render(request, "admin.html", status_code=503, enabled=False, message=None, error=None)
        if not hmac.compare_digest(password.encode(), expected.encode()):
            return render(request, "admin.html", status_code=403, enabled=True, message=None, error="비밀번호가 올바르지 않습니다.")
        await asyncio.to_thread(ctx.repo.purge_all)
        logger.info("관리자가 전체 데모 데이터를 정리했습니다")
        return render(request, "admin.html", enabled=True, message="전체 데모 데이터를 정리했습니다.", error=None)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        storage_ok = ctx.storage_ready and ctx.repo is not None and await asyncio.to_thread(ctx.repo.ping)
        ai_ok = bool(ctx.runner and ctx.runner.available)
        body = {"storage": "ok" if storage_ok else "unavailable", "ai": "ok" if ai_ok else "unavailable"}
        return JSONResponse(body, status_code=200 if storage_ok and ai_ok else 503)

    return app


app = create_app()
