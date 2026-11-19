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
_BRAND = re.compile(r"'?한[\s.,]*통[\s.,]*[이의][\s.,]*면[\s.,]*[대돼되][\s.,]*유우*~*'?")  # '한통이. 면 대유'처럼 끊겨도, 첫인사의 '돼유우~'도


def fix_name(text: str) -> str:
    """AI 목소리를 받아 적으면 서비스 이름이 '한통의면 대유'처럼 틀린다. 우리 이름으로 바로잡는다."""
    text = _BRAND.sub("'한 통이면 돼유'", text or "")
    return re.sub(r"'한 통이면 돼유'(?=[0-9A-Za-z가-힣])", "'한 통이면 돼유' ", text)


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
                           "started": now, "updated": now, "ended": None, "turns": [], "ai_seen": 0, "ai_last": None,
                           "request_id": None, "skipped": False}
    c["updated"] = now
    return c


def _last(turns: list[dict], role: str) -> int:
    return max((i for i, t in enumerate(turns) if t["role"] == role), default=-1)


def _transcript(c: dict, role: str, final: bool, text: str) -> None:
    turns = c["turns"]
    if role == "ai":
        text = fix_name(text)
    me, other = _last(turns, role), _last(turns, "elder" if role == "ai" else "ai")
    if me > other:  # 상대가 끼어들기 전, 이번 차례 말풍선에 이어 쓴다
        t = turns[me]
        if t.get("exact"):
            # AI 원문은 말하는 동안 앞 문장부터 온다. 원문에 아직 없는 뒷말만 받아 적은 글자로 잇고,
            # 원문이 늘어나면(_conversation) 원문으로 바뀐다.
            if _covered(text, t["text"]) >= 0.6:
                return
            if final:
                t["text"], t["partial"] = f"{t['text']} {text}".strip(), ""
            else:
                t["partial"] = text
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
    said = [fix_name(m["content"].strip()) for m in convo
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip()]
    seen, last = c["ai_seen"], c["ai_last"]
    # 지금 말하는 마지막 문장은 말하는 동안 길어진다(첫 문장만 먼저 온다) → 같은 말풍선을 늘린다
    if last is not None and 0 < seen <= len(said) and said[seen - 1] != last["text"] \
            and _covered(last["text"], said[seen - 1]) >= 0.6:
        last["text"] = said[seen - 1]
        if _covered(last["partial"], last["text"]) >= 0.6:
            last["partial"] = ""
    for exact in said[seen:]:
        raw = next((t for t in c["turns"] if t["role"] == "ai" and not t.get("exact")
                    and _covered(t["text"] + t["partial"], exact) >= 0.6), None)
        if raw:
            raw.update(text=exact, partial="", exact=True)
        else:  # 아직 말하기 전에 원문이 먼저 왔다
            raw = {"role": "ai", "text": exact, "partial": "", "exact": True}
            c["turns"].append(raw)
        c["ai_last"] = raw
    c["ai_seen"] = max(seen, len(said))


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
    보고서의 대화가 가장 완전하므로(실시간 글자는 빠지거나 틀릴 수 있다) 화면 대화를 보고서 대화로 바꾼다."""
    with _LOCK:
        c = _get(msg)
        if c is None:
            return
        _end(c)
        if turns:
            c["turns"] = [{"role": t["role"], "text": fix_name(t["text"]) if t["role"] == "ai" else t["text"], "partial": ""}
                          for t in turns]
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
