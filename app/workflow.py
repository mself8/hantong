"""신청서 초안 뒤의 업무 흐름.

기관별 신청서 작성 → 읍·면 공무원 제출 → 기관 응답 → 확정 안내문 → 공무원 확인 후 발송.
신청서와 안내문은 정해진 양식에 값을 채워 만든다. LLM은 쓰지 않는다(지어낸 내용이 들어가지 않게).
"""
from __future__ import annotations

import csv
import re
from datetime import date
from functools import lru_cache

from .data import DATA, WEEKDAYS

AGENCIES = {
    "companion": {"label": "병원동행 신청서", "agency": "홍천군노인복지관", "contact": "033-430-8753"},
    "hope_taxi": {"label": "희망택시 운행 요청서", "agency": "면사무소 희망택시 담당", "contact": "033-430-2975"},
    "special_transport": {"label": "특별교통수단 이용 신청서", "agency": "광역이동지원센터", "contact": "1577-2014"},
}
APP_STATUSES = {"draft": "작성됨", "submitted": "제출함", "confirmed": "확정", "rejected": "불가"}


@lru_cache
def hope_taxi_villages() -> list[dict]:
    """확인된 희망택시 운행 마을. 행정리(생곡1리)를 법정리(생곡리)로도 맞출 수 있게 둔다."""
    with open(DATA / "public/희망택시_확인된_운행마을.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["법정리"] = re.sub(r"\d+리$", "리", r["리"])
    return rows


def _hope_taxi_match(village_label: str | None) -> dict | None:
    if not village_label or " " not in village_label:
        return None
    town, ri = village_label.split(" ", 1)
    return next((r for r in hope_taxi_villages() if r["읍면"] == town and r["법정리"] == ri), None)


def _date_label(d: str | None) -> str:
    if not d:
        return "확인 필요"
    day = date.fromisoformat(d)
    return f"{day.month}월 {day.day}일({WEEKDAYS[day.weekday()]})"


def _hospital_label(req: dict) -> str:
    hosp = req.get("hospital") or {}
    cands = hosp.get("candidates") or []
    if hosp.get("status") == "matched" and cands:
        return cands[0]["name"]
    if cands:
        return "확인 필요 (후보: " + ", ".join(c["name"] for c in cands) + ")"
    return "확인 필요"


def _event(status: str, note: str = "") -> dict:
    from .pipeline import now_kst
    return {"at": now_kst().isoformat(timespec="seconds"), "status": status, "note": note}


def _app(kind: str, idx: int, fields: dict, notes: list[str]) -> dict:
    return {"id": f"A{idx}", "type": kind, **AGENCIES[kind], "status": "draft",
            "fields": fields, "notes": notes, "response": None, "history": [_event("draft", "자동 작성")]}


def build_applications(req: dict) -> list[dict]:
    """신청서 초안에서 필요한 기관별 신청서를 만든다."""
    if req.get("intent") != "hospital_visit":
        return []
    visit = req.get("visit") or {}
    resident = req.get("resident") or {}
    pickup = req.get("pickup") or {}
    companion = req.get("companion") or {}
    common = {
        "어르신": resident.get("name") or "명부 확인 필요",
        "나이": f"{resident['age']}세" if resident.get("age") else "확인 필요",
        "연락처": (req.get("call") or {}).get("caller") or "확인 필요",
        "사는 마을": pickup.get("village") or "확인 필요",
        "진료일": _date_label(visit.get("date")),
        "진료 시각": visit.get("time") or "모름",
        "병원": _hospital_label(req),
        "진료과": (req.get("hospital") or {}).get("department") or "모름",
    }
    apps: list[dict] = []
    deadline = [c["message"] for c in req.get("checks", []) if c["type"] == "companion_deadline"]

    if companion.get("needed") == "yes":
        apps.append(_app("companion", len(apps) + 1, {
            **common,
            "거동 상태": req.get("mobility") or resident.get("mobility") or "확인 필요",
            "동행이 필요한 이유": companion.get("reason") or "확인 필요",
            "요금": "첫 1시간 5,000원, 이후 30분마다 1,500원 (기초수급·차상위는 1,000원·500원)",
        }, deadline))

    taxi = _hope_taxi_match(pickup.get("village"))
    if taxi:
        town = taxi["읍면"]
        notes = [f"확인된 운행 구역: {taxi['리']} {taxi['세부 마을·반'] or ''}".strip(),
                 "희망택시는 읍·면 소재지까지 운행한다. 홍천읍 병원이면 소재지에서 버스로 갈아타야 한다."]
        apps.append(_app("hope_taxi", len(apps) + 1, {
            "마을": pickup.get("village"),
            "승차 지점": pickup.get("point") or "마을회관 (확인 필요)",
            "운행일": common["진료일"],
            "목적지": f"{town} 소재지" if town != "홍천읍" else "홍천읍내 병원",
            "인원": "1명" + (" + 동행 매니저" if companion.get("needed") == "yes" else ""),
            "왕복 여부": "왕복",
        }, notes))

    if req.get("wheelchair_needed") == "yes":
        apps.append(_app("special_transport", len(apps) + 1, {
            **common,
            "출발지": pickup.get("village") or "확인 필요",
            "목적지": common["병원"],
            "휠체어": "필요",
            "교통약자 등록": "확인 필요",
        }, ["특별교통수단은 교통약자로 미리 등록한 사람만 쓸 수 있다."]))
    return apps


def refresh_status(req: dict) -> None:
    """기관별 신청서 상태로 신청 전체 상태를 정한다. 보류와 안내 완료는 그대로 둔다."""
    if req.get("status") in ("held", "notified", "processing"):
        return
    apps = req.get("applications") or []
    states = {a["status"] for a in apps}
    if not apps or states <= {"confirmed", "rejected"}:
        if apps and req.get("message") is None:
            req["message"] = {"status": "draft", "text": confirmation_text(req), "channel": "기록",
                              "history": [_event("draft", "확정 안내문 자동 작성")]}
        req["status"] = "confirmed" if apps else req.get("status", "pending")
    elif "draft" in states:
        req["status"] = "pending"
    else:
        req["status"] = "submitted"


SHORT = {"companion": "병원동행", "hope_taxi": "희망택시", "special_transport": "특별교통수단"}


def _topic(word: str) -> str:
    """받침에 맞춰 은/는을 붙인다."""
    last = word[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + ("은" if has_final else "는")


def confirmation_text(req: dict) -> str:
    """기관 응답으로 어르신께 보낼 확정 안내문을 만든다. 표준어 존댓말, 짧게."""
    name = (req.get("resident") or {}).get("name")
    visit = req.get("visit") or {}
    who = f"{name} 어르신" if name else "어르신"
    lines = [f"{who}, {_date_label(visit.get('date'))} {_hospital_label(req)} 가시는 날 안내드립니다."]
    for a in req.get("applications") or []:
        resp = a.get("response") or {}
        if a["status"] == "confirmed":
            lines.append(f"{SHORT[a['type']]}: {resp.get('detail') or '확정되었습니다.'}")
        elif a["status"] == "rejected":
            lines.append(f"{_topic(SHORT[a['type']])} 이번에는 어렵습니다. 담당자가 다시 연락드리겠습니다.")
    lines.append("전날 다시 연락드리겠습니다.")
    return "\n".join(lines)
