"""통화 기록 → 신청서. 저장, 항목 뽑기, 목록 맞추기, 규칙 점검을 차례로 한다."""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from . import store
from .data import residents
from .normalize import match_hospital, match_village, resolve_date, resolve_time
from .validate import run_checks
from .workflow import build_applications, make_summary, refresh_status

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def _digits(num: str | None) -> str:
    d = re.sub(r"\D", "", num or "")
    return "0" + d[2:] if d.startswith("82") else d


def mask_phone(num: str | None) -> str | None:
    d = _digits(num)
    return f"{d[:3]}-****-{d[-4:]}" if len(d) >= 8 else None


def _demo_callers() -> dict[str, str]:
    """시연용 전화번호 -> 가상 어르신 ID. 환경변수 DEMO_CALLERS="web:R006,01012345678:R001" (레포에 넣지 않는다)."""
    out = {}
    for pair in os.getenv("DEMO_CALLERS", "").split(","):
        if ":" in pair:
            num, rid = pair.split(":", 1)
            out[_digits(num) or num.strip()] = rid.strip()
    return out


def _resident_view(r: dict) -> dict:
    return {
        "resident_id": r["resident_id"],
        "name": r["이름(마스킹)"],
        "age": int(r["나이"]) if r.get("나이", "").isdigit() else None,
        "sex": r.get("성별"),
        "village": f"{r['읍면']} {r['리']}",
        "pickup_point": r.get("승차지점"),
        "mobility": r.get("거동상태"),
        "ltc_grade": r.get("장기요양등급"),
        "guardian": r.get("보호자"),
    }


def find_resident(caller: str | None) -> dict | None:
    rid = _demo_callers().get(_digits(caller) or "web")  # 웹 통화(Vapi 화면에서 건 통화)는 번호가 없다
    r = next((x for x in residents() if x["resident_id"] == rid), None) if rid else None
    return _resident_view(r) if r else None


def _event(status: str, note: str = "") -> dict:
    return {"at": now_kst().isoformat(timespec="seconds"), "status": status, "note": note}


def new_request(turns: list[dict], *, source: str, call: dict | None = None,
                caller: str | None = None, created: datetime | None = None,
                prefill: dict | None = None) -> dict:
    """통화가 끝나자마자 기록부터 저장한다. 항목 뽑기는 process()가 뒤이어 한다."""
    created = created or now_kst()
    req = {
        "id": "Q" + created.strftime("%m%d") + "-" + secrets.token_hex(2).upper(),
        "created_at": created.isoformat(timespec="seconds"),
        "status": "processing",
        "source": source,
        "call": {**(call or {}), "caller": mask_phone(caller)},
        "resident": find_resident(caller),
        "transcript": turns,
        "intent": None,
        "summary": None,
        "hospital": None,
        "visit": None,
        "companion": None,
        "pickup": None,
        "mobility": None,
        "evidence": {},
        "checks": [],
        "memo": "",
        "prefill": prefill,
        "wheelchair_needed": None,
        "applications": [],
        "message": None,
        "history": [_event("processing", "통화 기록 받음")],
    }
    return store.save(req)


def fill(req: dict, ex, today: date) -> dict:
    """뽑은 항목(ex)을 실제 목록에 맞추고 규칙으로 점검해 신청서에 채운다."""
    resident = req.get("resident")
    roster_village = match_village(resident["village"]) if resident else None
    village = match_village(ex.village) or roster_village
    same_as_roster = bool(roster_village and village and roster_village.code == village.code)
    home_town = village.town if village else None
    hosp = match_hospital(ex.hospital_query, ex.department, home_town, village.name if village else None)

    rule_date = resolve_date(ex.date_expr, today)
    visit_date = rule_date.isoformat() if rule_date else ex.date
    extra = []
    if roster_village and village and not same_as_roster:
        extra.append({"type": "village_mismatch", "level": "warn",
                      "message": f"명부에는 {roster_village.label}인데 통화에서는 {village.label}라고 했어요. 확인이 필요해요."})
    if rule_date and ex.date and rule_date.isoformat() != ex.date:
        extra.append({"type": "date_mismatch", "level": "info",
                      "message": f"날짜 계산이 엇갈려 규칙으로 계산한 {rule_date.isoformat()}를 썼어요 (AI 계산: {ex.date})."})

    req.update({
        "intent": ex.intent,
        "summary": ex.summary,
        "hospital": hosp,
        "visit": {"date_expr": ex.date_expr, "date": visit_date,
                  "date_source": "rule" if rule_date else ("ai" if ex.date else None),
                  "time_expr": ex.time_expr, "time": resolve_time(ex.time_expr)},
        "companion": {"needed": ex.companion_needed, "reason": ex.companion_reason},
        "pickup": {"village_expr": ex.village, "village": village.label if village else None,
                   "village_code": village.code if village else None,
                   "point": resident.get("pickup_point") if same_as_roster else None},
        "mobility": ex.mobility or (resident.get("mobility") if resident else None),
        "wheelchair_needed": ex.wheelchair_needed,
        "evidence": {"hospital": ex.evidence_hospital, "date": ex.evidence_date,
                     "companion": ex.evidence_companion, "village": ex.evidence_village},
    })
    req["checks"] = run_checks(req, today) + extra
    req["summary"] = make_summary(req, ex.summary)
    req["applications"] = build_applications(req)
    req["message"] = None
    return req


def process(rid: str, extractor=None) -> dict:
    """저장된 통화 기록에서 신청서를 만든다. 실패해도 기록은 남기고 '확인 대기'로 둔다."""
    from .extract import extract, from_dict

    req = store.get(rid)
    today = date.fromisoformat(req["created_at"][:10])
    try:
        if req.get("prefill") and extractor is None:
            ex, how = from_dict(req["prefill"]), "Vapi가 뽑은 항목으로"
        else:
            ex, how = (extractor or extract)(req["transcript"], today), "서버 LLM으로"
        fill(req, ex, today)
        req["history"].append(_event("pending", f"신청서 초안 작성 ({how})"))
    except Exception as e:  # 공무원이 통화 기록을 보고 직접 처리할 수 있게 남긴다
        req["checks"] = [{"type": "extract_failed", "level": "warn",
                          "message": f"자동 정리에 실패했어요. 통화 기록을 직접 확인해 주세요. ({type(e).__name__}: {e})"}]
        req["history"].append(_event("pending", "자동 정리 실패"))
    req["status"] = "pending"
    refresh_status(req)
    return store.save(req)


def decide(rid: str, status: str, memo: str = "") -> dict | None:
    req = store.get(rid)
    if not req:
        return None
    req["status"] = status
    if memo:
        req["memo"] = memo
    req["history"].append(_event(status, memo))
    return store.save(req)


def parse_text(text: str) -> list[dict]:
    """'어르신: …' / 'AI: …' 줄로 된 글을 통화 기록으로 바꾼다. 표시가 없으면 어르신 말 한 줄로 본다."""
    turns = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(어르신|사용자|user|User|AI|ai|상담원)\s*[:：]\s*(.*)$", line)
        if m:
            turns.append({"role": "ai" if m.group(1) in ("AI", "ai", "상담원") else "elder", "text": m.group(2)})
        else:
            turns.append({"role": "elder", "text": line})
    return turns


def vapi_structured(msg: dict) -> dict | None:
    """Vapi 구조화 출력(통화 뒤 GPT가 신청서 형식대로 뽑은 값)을 찾는다. 없으면 None."""
    artifact = msg.get("artifact") or (msg.get("call") or {}).get("artifact") or {}
    outputs = artifact.get("structuredOutputs") or msg.get("structuredOutputs") or {}
    items = list(outputs.values()) if isinstance(outputs, dict) else list(outputs)
    for it in items:
        result = it.get("result", it) if isinstance(it, dict) else None
        if isinstance(result, str):  # 결과가 JSON 글로 올 때
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if isinstance(result, dict) and "intent" in result:
            return result
    data = (msg.get("analysis") or {}).get("structuredData")
    return data if isinstance(data, dict) and "intent" in data else None


def parse_vapi_report(msg: dict) -> tuple[list[dict], dict, str | None]:
    """Vapi end-of-call-report에서 통화 기록, 통화 정보, 건 사람 번호를 꺼낸다."""
    artifact = msg.get("artifact") or {}
    turns = []
    for m in artifact.get("messages") or msg.get("messages") or []:
        text = (m.get("message") or m.get("content") or "").strip()
        if not text:
            continue
        if m.get("role") == "user":
            turns.append({"role": "elder", "text": text})
        elif m.get("role") in ("bot", "assistant"):
            turns.append({"role": "ai", "text": text})
    if not turns:
        turns = parse_text((artifact.get("transcript") or msg.get("transcript") or "").replace("User:", "어르신:"))
    call = msg.get("call") or {}
    info = {
        "call_id": call.get("id"),
        "started_at": msg.get("startedAt") or call.get("startedAt"),
        "ended_at": msg.get("endedAt") or call.get("endedAt"),
        "ended_reason": msg.get("endedReason"),
        "recording_url": artifact.get("recordingUrl") or msg.get("recordingUrl"),
    }
    caller = (call.get("customer") or {}).get("number") or (msg.get("customer") or {}).get("number")
    return turns, info, caller


# ── 공무원 처리: 병원 고르기, 신청서 제출, 기관 응답, 확정 안내문 ─────────

class FlowError(Exception):
    """화면에서 할 수 없는 순서로 누른 경우."""


def _load(rid: str) -> dict:
    req = store.get(rid)
    if not req:
        raise KeyError(rid)
    return req


def _app_of(req: dict, aid: str) -> dict:
    app = next((a for a in req.get("applications") or [] if a["id"] == aid), None)
    if not app:
        raise KeyError(aid)
    return app


def choose_hospital(rid: str, hosp_id: str) -> dict:
    """후보 중 병원 하나를 고른다. 아직 제출하지 않은 신청서는 새 병원으로 다시 채운다."""
    from .data import hospital_by_id
    req = _load(rid)
    h = hospital_by_id().get(hosp_id)
    if not h:
        raise KeyError(hosp_id)
    req["hospital"] = {**(req.get("hospital") or {}), "status": "matched",
                       "candidates": [{"id": h.id, "name": h.name, "kind": h.kind,
                                       "town": h.town or h.sigungu, "far": h.far}]}
    today = date.fromisoformat(req["created_at"][:10])
    extra = [c for c in req.get("checks", []) if c["type"] in ("village_mismatch", "date_mismatch")]
    req["checks"] = run_checks(req, today) + extra
    req["summary"] = make_summary(req, req.get("summary"))
    if (req.get("message") or {}).get("status") != "sent":
        req["message"] = None  # 바뀐 병원으로 안내문을 다시 만든다
    kept = [a for a in req.get("applications") or [] if a["status"] != "draft"]
    fresh = [a for a in build_applications(req) if a["type"] not in {k["type"] for k in kept}]
    req["applications"] = kept + fresh
    for i, a in enumerate(req["applications"], 1):
        a["id"] = f"A{i}"
    req["history"].append(_event(req["status"], f"병원 선택: {h.name}"))
    refresh_status(req)
    return store.save(req)


def edit_application(rid: str, aid: str, fields: dict) -> dict:
    req = _load(rid)
    app = _app_of(req, aid)
    if app["status"] != "draft":
        raise FlowError("제출한 신청서는 고칠 수 없어요.")
    app["fields"].update({k: str(v) for k, v in fields.items()})
    app["history"].append(_event("draft", "공무원이 고침"))
    return store.save(req)


def submit_application(rid: str, aid: str | None = None) -> dict:
    """신청서를 기관에 제출한다. aid가 없으면 작성된 신청서를 모두 제출한다."""
    req = _load(rid)
    targets = [_app_of(req, aid)] if aid else [a for a in req.get("applications") or [] if a["status"] == "draft"]
    if not targets or any(a["status"] != "draft" for a in targets):
        raise FlowError("제출할 신청서가 없어요.")
    for a in targets:
        a["status"] = "submitted"
        a["history"].append(_event("submitted", f"{a['agency']}에 제출"))
    req["history"].append(_event(req["status"], "신청서 제출: " + ", ".join(a["label"] for a in targets)))
    refresh_status(req)
    return store.save(req)


def respond_application(rid: str, aid: str, result: str, detail: str = "") -> dict:
    """기관 응답을 기록한다. 시연에서는 대시보드의 '기관 응답' 버튼이 대신 누른다."""
    if result not in ("confirmed", "rejected"):
        raise FlowError("응답은 confirmed나 rejected여야 해요.")
    req = _load(rid)
    app = _app_of(req, aid)
    if app["status"] != "submitted":
        raise FlowError("제출한 신청서에만 응답할 수 있어요.")
    app["status"] = result
    app["response"] = {"result": result, "detail": detail}
    app["history"].append(_event(result, detail))
    req["history"].append(_event(req["status"], f"{app['agency']} 응답: {'확정' if result == 'confirmed' else '불가'}"))
    refresh_status(req)
    return store.save(req)


def edit_message(rid: str, text: str) -> dict:
    req = _load(rid)
    msg = req.get("message") or {"status": "draft", "channel": "기록", "history": []}
    if msg["status"] == "sent":
        raise FlowError("이미 보낸 안내문이에요.")
    msg.update(text=text, status="draft")
    msg["history"].append(_event("draft", "공무원이 고침"))
    req["message"] = msg
    return store.save(req)


def send_message(rid: str) -> dict:
    """확정 안내문을 보낸다. 시연에서는 발송 기록만 남긴다(실제 문자·전화 발송은 나중에)."""
    req = _load(rid)
    msg = req.get("message")
    if not msg or not msg.get("text"):
        raise FlowError("보낼 안내문이 없어요.")
    if msg["status"] == "sent":
        raise FlowError("이미 보낸 안내문이에요.")
    msg["status"] = "sent"
    msg["history"].append(_event("sent", f"{msg.get('channel', '기록')}으로 발송"))
    req["status"] = "notified"
    req["history"].append(_event("notified", "어르신께 확정 안내 발송"))
    return store.save(req)
