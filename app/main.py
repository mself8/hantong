"""한 통이면 돼유 서버: Vapi 통화 기록을 받아 신청서를 만들고, 대시보드에 API를 준다."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pipeline, store
from .data import hospitals, villages

load_dotenv()  # 로컬에서만 .env를 읽는다. Railway는 환경변수를 직접 넣는다.

app = FastAPI(title="한 통이면 돼유")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATUSES = {"processing": "정리 중", "pending": "확인 대기", "approved": "승인", "held": "보류"}


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
    req = pipeline.new_request(turns, source="phone", call=call, caller=caller)
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


@app.post("/api/requests/{rid}/approve")
def approve(rid: str, body: DecisionIn | None = None) -> dict:
    req = pipeline.decide(rid, "approved", body.memo if body else "")
    if not req:
        raise HTTPException(status_code=404, detail="없는 신청서")
    return req


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


# ── 대시보드 화면 ─────────────────────────────────────

DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if (DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="dashboard")
else:
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return "<h1>한 통이면 돼유</h1><p>대시보드 준비 중. API 문서: <a href='/docs'>/docs</a></p>"
