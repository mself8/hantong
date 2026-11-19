"""통화 중 대화를 화면에 실시간으로 보여 주는 메모리 저장소.

Vapi가 통화 중에 서버로 보내는 메시지로 말풍선 목록을 만든다. 웹 통화와 실제 전화가 같은 길로 들어온다.
서버를 다시 켜면 비워진다(신청서는 따로 DB에 남는다).

- transcript: 받아 적은 글자. 말하는 중에는 부분 글자(partial), 말이 끝나면 완성 글자(final)가 온다.
- conversation-update: AI가 실제로 만든 문장 원문. AI 목소리를 다시 받아 적은 글자에는
  '돼유 → 대유' 같은 오타가 생겨서, 원문이 오면 그 말풍선을 원문으로 바꾼다.
- status-update(ended)·end-of-call-report: 통화 끝. 보고서가 만든 신청서 번호를 붙인다.
"""
from __future__ import annotations

import difflib
import re
import threading
import time

_LOCK = threading.Lock()
_CALLS: dict[str, dict] = {}
KEEP_SECONDS = 2 * 3600  # 두 시간 지난 통화는 지운다
_NOT_CHAR = re.compile(r"[^0-9A-Za-z가-힣]")


def _norm(s: str) -> str:
    return _NOT_CHAR.sub("", s or "")


def _covered(raw: str, exact: str) -> float:
    """받아 적은 글자(raw) 가운데 원문(exact)과 순서대로 겹치는 글자의 비율."""
    a, b = _norm(raw), _norm(exact)
    if not a:
        return 0.0
    blocks = difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    return sum(x.size for x in blocks) / len(a)


def _get(msg: dict) -> dict | None:
    call = msg.get("call") or {}
    cid = call.get("id")
    if not cid:
        return None
    now = time.time()
    c = _CALLS.get(cid)
    if c is None:
        for old in [k for k, v in _CALLS.items() if now - v["updated"] > KEEP_SECONDS]:
            del _CALLS[old]
        c = _CALLS[cid] = {"id": cid, "kind": "phone" if "phone" in (call.get("type") or "").lower() else "web",
                           "started": now, "updated": now, "ended": None, "turns": [], "ai_seen": 0,
                           "request_id": None, "skipped": False}
    c["updated"] = now
    return c


def _last(turns: list[dict], role: str) -> int:
    return max((i for i, t in enumerate(turns) if t["role"] == role), default=-1)


def _transcript(c: dict, role: str, final: bool, text: str) -> None:
    turns = c["turns"]
    me, other = _last(turns, role), _last(turns, "elder" if role == "ai" else "ai")
    if me > other:  # 상대가 끼어들기 전, 이번 차례 말풍선에 이어 쓴다
        t = turns[me]
        if t.get("exact"):  # AI 원문으로 이미 채운 차례면 받아 적은 글자는 버린다
            return
    else:
        if other >= 0:
            turns[other]["partial"] = ""  # 상대가 말을 시작했으면 남은 부분 글자는 버린다
        t = {"role": role, "text": "", "partial": ""}
        turns.append(t)
    if final:
        t["text"] = f"{t['text']} {text}".strip()
        t["partial"] = ""
    elif _norm(text) not in _norm(t["text"]):  # 늦게 도착한 부분 글자(이미 끝난 말의 앞부분)는 무시
        t["partial"] = text


def _conversation(c: dict, msg: dict) -> None:
    convo = msg.get("messagesOpenAIFormatted") or msg.get("conversation") or []
    said = [m["content"].strip() for m in convo
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip()]
    for exact in said[c["ai_seen"]:]:
        raw = next((t for t in c["turns"] if t["role"] == "ai" and not t.get("exact")
                    and _covered(t["text"] + t["partial"], exact) >= 0.6), None)
        if raw:
            raw.update(text=exact, partial="", exact=True)
        else:  # 아직 말하기 전에 원문이 먼저 왔다
            c["turns"].append({"role": "ai", "text": exact, "partial": "", "exact": True})
    c["ai_seen"] = max(c["ai_seen"], len(said))


def _end(c: dict) -> None:
    if c["ended"] is None:
        c["ended"] = time.time()
        for t in c["turns"]:
            if t["partial"] and _norm(t["partial"]) not in _norm(t["text"]):
                t["text"] = f"{t['text']} {t['partial']}".strip()
            t["partial"] = ""


def update(msg: dict) -> None:
    """통화 중 메시지 하나를 반영한다."""
    kind = msg.get("type") or ""
    with _LOCK:
        c = _get(msg)
        if c is None:
            return
        if kind.startswith("transcript"):
            text = (msg.get("transcript") or "").strip()
            if text and c["ended"] is None:
                _transcript(c, "ai" if msg.get("role") == "assistant" else "elder",
                            msg.get("transcriptType") == "final", text)
        elif kind == "conversation-update":
            _conversation(c, msg)
        elif kind == "status-update" and msg.get("status") == "ended":
            _end(c)


def finish(msg: dict, request_id: str | None, turns: list[dict]) -> None:
    """통화 끝 보고서가 오면 끝으로 표시하고 신청서 번호를 붙인다.
    통화 중 메시지를 못 받았으면(설정이 꺼져 있으면) 보고서의 대화로 채운다."""
    with _LOCK:
        c = _get(msg)
        if c is None:
            return
        _end(c)
        if not c["turns"]:
            c["turns"] = [{"role": t["role"], "text": t["text"], "partial": ""} for t in turns]
        c["request_id"], c["skipped"] = request_id, request_id is None


def snapshot(cid: str | None = None) -> dict | None:
    """화면에 보낼 한 통화의 상태. cid가 없으면 가장 최근에 시작한 통화."""
    with _LOCK:
        c = _CALLS.get(cid) if cid else max(_CALLS.values(), key=lambda v: v["started"], default=None)
        if c is None:
            return None
        now = time.time()
        return {"id": c["id"], "kind": c["kind"], "ended": c["ended"] is not None,
                "elapsed": int((c["ended"] or now) - c["started"]),
                "ended_ago": int(now - c["ended"]) if c["ended"] else None,
                "request_id": c["request_id"], "skipped": c["skipped"],
                "turns": [{"role": t["role"], "text": t["text"], "partial": t["partial"]} for t in c["turns"]]}
