"""한 통이면 돼유 서버: Vapi 통화 기록을 받아 신청서를 만들고, 대시보드에 API를 준다."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import live, pipeline, store, view
from .data import hospitals, villages, voice_cards

mimetypes.add_type("font/woff2", ".woff2")  # 글꼴 파일을 글꼴 형식으로 내보낸다
load_dotenv()  # 로컬에서만 .env를 읽는다. Railway는 환경변수를 직접 넣는다.

app = FastAPI(title="한 통이면 돼유")
log = logging.getLogger("uvicorn.error")


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    """화면(HTML)은 브라우저가 저장해 둔 옛 판을 쓰지 않고 늘 새로 확인하게 한다."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATUSES = {"processing": "정리 중", "pending": "확인 대기", "submitted": "기관 회신 대기",
            "confirmed": "안내 대기", "notified": "안내 완료", "held": "보류"}


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ── Vapi ──────────────────────────────────────────────

@app.post("/vapi/webhook")
async def vapi_webhook(request: Request, bg: BackgroundTasks,
                       x_vapi_secret: str | None = Header(default=None)) -> dict:
    secret = os.getenv("VAPI_SECRET")
    if secret and x_vapi_secret != secret:
        raise HTTPException(status_code=401, detail="bad secret")
    body = await request.json()
    msg = body.get("message") or {}
    kind = msg.get("type") or ""
    if kind.startswith("transcript") or kind in ("conversation-update", "status-update"):
        live.update(msg)  # 통화 중 대화를 화면(/call, /live)에 실시간으로 보여 준다
        return {"ok": True}
    if kind != "end-of-call-report":
        return {"ok": True}
    turns, call, caller = pipeline.parse_vapi_report(msg)
    if not any(t["role"] == "elder" for t in turns):
        live.finish(msg, None, turns)
        return {"ok": True, "skipped": "어르신 말이 없는 통화"}
    prefill = pipeline.vapi_structured(msg)
    # 첫 통화 점검용: 내용은 남기지 않고 어떤 칸이 왔는지만 남긴다
    log.info("vapi report: artifact=%s structured=%s", sorted((msg.get("artifact") or {}).keys()), bool(prefill))
    req = pipeline.new_request(turns, source="phone", call=call, caller=caller, prefill=prefill)
    live.finish(msg, req["id"], turns)
    bg.add_task(pipeline.process, req["id"])
    return {"ok": True, "id": req["id"]}


def _live_view(s: dict | None) -> dict:
    if not s:
        return {"id": None}
    if s["request_id"]:  # 정리가 끝나면 담당자에게 넘어간 한 줄 요약을 붙인다
        req = store.get(s["request_id"])
        if req and req.get("status") != "processing":
            s["summary"] = req.get("summary")
    return s


@app.get("/api/live")
def live_latest() -> dict:
    """가장 최근에 시작한 통화의 대화 (발표 화면용)."""
    return _live_view(live.snapshot())


@app.get("/api/live/{cid}")
def live_call(cid: str) -> dict:
    return _live_view(live.snapshot(cid))


# ── 대시보드 API ──────────────────────────────────────

class TextIn(BaseModel):
    text: str
    caller: str | None = None


class DecisionIn(BaseModel):
    memo: str = ""


@app.post("/api/requests")
def create_from_text(body: TextIn) -> dict:
    """글로 된 통화 기록으로 신청서를 만든다. 시험과 평가에 쓴다."""
    turns = pipeline.parse_text(body.text)
    if not turns:
        raise HTTPException(status_code=400, detail="빈 기록")
    req = pipeline.new_request(turns, source="text", caller=body.caller)
    return pipeline.process(req["id"])


@app.get("/api/requests")
def list_requests(status: str | None = None) -> list[dict]:
    return store.list_all(status)


@app.get("/api/requests/{rid}")
def get_request(rid: str) -> dict:
    req = store.get(rid)
    if not req:
        raise HTTPException(status_code=404, detail="없는 신청서")
    return req


class HospitalIn(BaseModel):
    hosp_id: str


class FieldsIn(BaseModel):
    fields: dict


class RespondIn(BaseModel):
    result: str  # confirmed | rejected | note(기타 메모)
    detail: str = ""


class MessageIn(BaseModel):
    text: str


def _run(fn, *args):
    try:
        return fn(*args)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"찾을 수 없어요: {e}")
    except pipeline.FlowError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/requests/{rid}/hospital")
def choose_hospital(rid: str, body: HospitalIn) -> dict:
    return _run(pipeline.choose_hospital, rid, body.hosp_id)


@app.put("/api/requests/{rid}/applications/{aid}")
def edit_application(rid: str, aid: str, body: FieldsIn) -> dict:
    return _run(pipeline.edit_application, rid, aid, body.fields)


@app.post("/api/requests/{rid}/applications/{aid}/submit")
def submit_application(rid: str, aid: str) -> dict:
    return _run(pipeline.submit_application, rid, aid)


@app.post("/api/requests/{rid}/submit")
def submit_all(rid: str) -> dict:
    """작성된 기관별 신청서를 한 번에 제출한다."""
    return _run(pipeline.submit_application, rid, None)


@app.post("/api/requests/{rid}/applications/{aid}/respond")
def respond_application(rid: str, aid: str, body: RespondIn) -> dict:
    return _run(pipeline.respond_application, rid, aid, body.result, body.detail)


@app.put("/api/requests/{rid}/message")
def edit_message(rid: str, body: MessageIn) -> dict:
    return _run(pipeline.edit_message, rid, body.text)


@app.post("/api/requests/{rid}/message/send")
def send_message(rid: str) -> dict:
    return _run(pipeline.send_message, rid)


@app.post("/api/requests/{rid}/hold")
def hold(rid: str, body: DecisionIn | None = None) -> dict:
    req = pipeline.decide(rid, "held", body.memo if body else "")
    if not req:
        raise HTTPException(status_code=404, detail="없는 신청서")
    return req


@app.get("/api/statuses")
def statuses() -> dict:
    return STATUSES


@app.get("/api/hospitals")
def list_hospitals(q: str | None = None) -> list[dict]:
    hs = [h.to_dict() for h in hospitals()]
    return [h for h in hs if not q or q in h["name"]]


@app.get("/api/villages")
def list_villages() -> list[dict]:
    return [v.to_dict() for v in villages()]


# ── 시연용 웹 통화 페이지 (발표장 QR 코드) ──────────────

CALL_PAGE = Path(__file__).resolve().parent / "static" / "call.html"
PAPERLOGY = "".join(f"@font-face{{font-family:'Paperlogy';src:url('/fonts/Paperlogy-{w}.woff2') format('woff2');font-weight:{n}}}"
                    for w, n in (("4Regular", 400), ("6SemiBold", 600), ("7Bold", 700)))
DEFAULT_ASSISTANT = "e441e44c-5204-458d-a2d2-f8676774de27"


def _call_html(cfg: dict) -> str:
    script = f"<script>window.HANTONG = {json.dumps(cfg, ensure_ascii=False)};</script>"
    return CALL_PAGE.read_text(encoding="utf-8").replace("</head>", script + "\n</head>", 1)


@app.get("/call", response_class=HTMLResponse)
def call_page() -> str:
    """휴대폰 브라우저에서 AI 상담원과 바로 통화하는 페이지. Vapi 공개 키는 Railway 변수로만 받는다."""
    return _call_html({"publicKey": os.getenv("VAPI_PUBLIC_KEY", ""),
                       "assistantId": os.getenv("VAPI_ASSISTANT_ID", DEFAULT_ASSISTANT)})


@app.get("/live", response_class=HTMLResponse)
def live_page() -> str:
    """발표 화면용: 지금 걸려 온 통화(실제 전화 포함)의 대화를 실시간으로 보여 준다."""
    return _call_html({"mode": "live", "phone": os.getenv("HANTONG_PHONE", "+1 346-372-7261")})


@app.get("/api/voice-cards")
def list_voice_cards() -> list[dict]:
    return voice_cards()


@app.get("/call/cards", response_class=HTMLResponse)
def voice_card_list() -> str:
    """팀원 음성 시험용 카드 목록. 카드를 누르면 그 카드 대본이 보이는 통화 페이지로 간다."""
    from html import escape
    rows = "".join(
        f"<a class='c' href='/call?card={escape(c['call_id'])}'><b>{escape(c['call_id'])}</b>"
        f"<span>{escape(c['말하는_방식'])}</span><span>{escape(c['병원표현'])} · {escape(c['날짜표현'])}</span></a>"
        for c in voice_cards())
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'><title>음성 시험 카드</title>"
            "<style>" + PAPERLOGY + "body{margin:0;background:#f6f4ef;color:#1d2433;font-family:'Paperlogy','Apple SD Gothic Neo',sans-serif;word-break:keep-all}"
            "main{max-width:480px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 6px}p{color:#5b6475;margin:0 0 16px}"
            ".c{display:grid;grid-template-columns:48px 1fr;gap:2px 10px;background:#fff;border:1px solid #e3dfd6;border-radius:12px;"
            "padding:12px 14px;margin-bottom:8px;color:inherit;text-decoration:none}.c b{grid-row:span 2;font-size:18px}"
            ".c span:last-child{color:#5b6475;font-size:14px}</style></head><body><main><h1>음성 시험 카드 18장</h1>"
            "<p>한 사람이 카드 한 장씩 맡아 통화해 주세요. 카드를 누르면 대본이 보여요.</p>" + rows + "</main></body></html>")


@app.get("/api/requests/{rid}/forms.pdf")
def application_bundle(rid: str) -> Response:
    """통합지원 신청서 + 기관별 신청서(+ 필요하면 특별교통수단 등록 신청서)를 값을 채운 PDF 한 묶음으로 내려준다."""
    from . import pdfforms
    req = store.get(rid)
    if not req:
        raise HTTPException(status_code=404, detail="없는 신청서")
    name = f"신청서_{rid}.pdf"
    return Response(pdfforms.bundle(req), media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=\"forms_{rid}.pdf\"; filename*=UTF-8''{quote(name)}"})


@app.get("/forms/{rid}/{aid}", response_class=HTMLResponse)
def application_form(rid: str, aid: str) -> str:
    """기관별 신청서 한 장을 인쇄용 양식으로 보여 준다."""
    req = store.get(rid)
    app_ = next((a for a in (req or {}).get("applications") or [] if a["id"] == aid), None)
    if not app_:
        raise HTTPException(status_code=404, detail="없는 신청서")
    return view.form_page(req, app_)


@app.get("/view", response_class=HTMLResponse)
def view_page() -> str:
    """신청서·안내문 확인용 읽기 전용 화면 (대시보드와 별개로 항상 열린다)."""
    return view.page()


# ── 대시보드 화면 ─────────────────────────────────────

DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if (DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="dashboard")
else:
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return view.page()
