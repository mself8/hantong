"""한 통이면 돼유 서버: Vapi 통화 기록을 받아 신청서를 만들고, 대시보드에 API를 준다."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pipeline, store, view
from .data import hospitals, villages

load_dotenv()  # 로컬에서만 .env를 읽는다. Railway는 환경변수를 직접 넣는다.

app = FastAPI(title="한 통이면 돼유")
log = logging.getLogger("uvicorn.error")
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
    if msg.get("type") != "end-of-call-report":
        return {"ok": True}
    turns, call, caller = pipeline.parse_vapi_report(msg)
    if not any(t["role"] == "elder" for t in turns):
        return {"ok": True, "skipped": "어르신 말이 없는 통화"}
    prefill = pipeline.vapi_structured(msg)
    # 첫 통화 점검용: 내용은 남기지 않고 어떤 칸이 왔는지만 남긴다
    log.info("vapi report: artifact=%s structured=%s", sorted((msg.get("artifact") or {}).keys()), bool(prefill))
    req = pipeline.new_request(turns, source="phone", call=call, caller=caller, prefill=prefill)
    bg.add_task(pipeline.process, req["id"])
    return {"ok": True, "id": req["id"]}


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
    result: str  # confirmed | rejected
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
DEFAULT_ASSISTANT = "e441e44c-5204-458d-a2d2-f8676774de27"


@app.get("/call", response_class=HTMLResponse)
def call_page() -> str:
    """휴대폰 브라우저에서 AI 상담원과 바로 통화하는 페이지. Vapi 공개 키는 Railway 변수로만 받는다."""
    cfg = {"publicKey": os.getenv("VAPI_PUBLIC_KEY", ""),
           "assistantId": os.getenv("VAPI_ASSISTANT_ID", DEFAULT_ASSISTANT)}
    script = f"<script>window.HANTONG = {json.dumps(cfg)};</script>"
    return CALL_PAGE.read_text(encoding="utf-8").replace("</head>", script + "\n</head>", 1)


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
