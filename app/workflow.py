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
    "companion": {"label": "병원동행 예약", "agency": "홍천군노인복지관", "contact": "033-430-8753"},
    "hope_taxi": {"label": "희망택시 운행 요청", "agency": "면사무소 희망택시 담당", "contact": "033-430-2975"},
    "special_transport": {"label": "특별교통수단 예약", "agency": "광역이동지원센터", "contact": "1577-2014"},
}
APP_STATUSES = {"draft": "요청 전", "submitted": "요청함", "confirmed": "확정", "rejected": "불가"}
# 어르신이 내는 요금. 출처: 병원동행 = 아시아경제 2026-01-15·홍천군노인복지관 안내, 희망택시 = 조례 제4조②·홍천 농어촌버스 요금표,
# 특별교통수단 = 공공데이터포털 교통약자 이동지원센터 표준데이터(2023-10-05)
COMPANION_FEE = {True: "첫 1시간 1,000원, 이후 30분마다 500원(기초수급·차상위). 차비는 따로예요",
                 False: "첫 1시간 5,000원, 이후 30분마다 1,500원. 차비는 따로예요"}
PRICE = {
    "companion": "첫 1시간 5,000원, 이후 30분마다 1,500원(기초수급·차상위는 1,000원·500원). 차비는 따로예요",
    "hope_taxi": "1,700원(버스 기본요금과 같아요)",
    "special_transport": "4km까지 1,100원, 넘으면 1km마다 100원",
}


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


def elder_name(req: dict) -> str | None:
    """어르신 이름: 통화에서 들은 성함이 먼저, 없으면 이 번호로 등록된 이름."""
    return req.get("caller_name") or (req.get("resident") or {}).get("name")


def pickup_address(pickup: dict) -> str | None:
    """카카오맵에서 찾은 모시러 갈 곳의 실제 주소. 예: '강원특별자치도 홍천군 서석면 ○○길 12 (수하리마을회관) 앞'"""
    m = pickup.get("map")
    return f"{m['address']} ({m['name']}){m['tail']}" if m else None


def form_address(pickup: dict) -> str:
    """대시보드 주소 칸과 공식 신청서 주소 칸에 같이 쓰는 주소. 지도에서 찾은 실제 주소가 먼저, 없으면 마을과 들은 말."""
    found = pickup_address(pickup)
    if found:
        return found
    base = f"강원특별자치도 홍천군 {pickup['village']}" if pickup.get("village") else "강원특별자치도 홍천군 (마을 확인 필요)"
    return base + (f" ({pickup['detail']})" if pickup.get("detail") else "")


def _transport(req: dict, why_place: str | None) -> dict:
    """교통수단: 통화에서 받은 휠체어 여부와 사는 마을로 정한다(어르신께 따로 묻지 않는다)."""
    p, res = req.get("pickup") or {}, req.get("resident") or {}
    if req.get("wheelchair_needed") == "yes":
        reg = {True: "등록됨", False: "미등록이라 등록 필요"}.get(res.get("special_transport"), "등록 여부 확인")
        return {"value": "특별교통수단", "sub": f"휠체어 차량 · {reg}", "level": "ok"}
    card = {True: "교통복지카드 있음(월 20회 무료)", False: "교통복지카드 없음"}.get(res.get("bus_card"), "교통복지카드 확인")
    bus = req.get("bus_ok") or "unknown"
    taxi = _hope_taxi_match(p.get("village"))
    if taxi and bus != "yes":                      # 버스가 어렵거나 못 들었으면, 희망택시가 다니는 마을이니 희망택시
        town = taxi["읍면"]
        return {"value": "희망택시", "sub": "읍내 병원까지" if town == "홍천읍" else f"{town} 소재지까지, 이후 버스",
                "level": "check" if why_place else "ok", "why": why_place}
    if bus == "no":
        return {"value": "교통편 확인 필요", "sub": "버스를 타기 어렵다고 하셨어요", "level": "check",
                "why": "희망택시가 다니지 않는 마을이라 다른 교통편을 알아봐야 해요"}
    if not p.get("village"):
        return {"value": "농어촌버스", "sub": card, "level": "check", "why": "사는 곳을 몰라 교통수단을 정하지 못했어요"}
    why = why_place or (None if bus == "yes" else "버스를 타실 수 있는지 듣지 못했어요")
    return {"value": "농어촌버스", "sub": card + (" · 희망택시도 다니는 마을" if taxi else ""), "level": "check" if why else "ok", "why": why}


def facts(req: dict) -> dict:
    """대시보드 맨 위 여섯 칸. 값은 늘 채우고, 믿을 만한 정도는 점수가 아니라 값이 어디서 왔는지로 정한다.
    ok = 어르신이 말했거나 상담원 제안에 동의한 값(날짜는 규칙 계산이 AI 계산과 같을 때)
    check = 우리가 고른 값(아픈 곳으로 고른 병원, 명부 마을), 계산이 엇갈린 날짜, '오후쯤'처럼 하나로 안 정해진 말
    missing = 통화에서 아예 안 나온 값. 지어내지 않는다."""
    v, h, p = req.get("visit") or {}, req.get("hospital") or {}, req.get("pickup") or {}
    checks = {c["type"] for c in req.get("checks") or []}
    f: dict[str, dict] = {}

    if v.get("date"):
        why = ("통화에서 확인한 날짜와 계산이 엇갈려요" if "date_mismatch" in checks
               else None if v.get("date_source") == "rule" else "AI가 계산한 날짜예요")
        label, expr = _date_label(v["date"]), v.get("date_expr") or ""
        f["date"] = {"value": label, "sub": "" if label.startswith(expr) else expr,  # '11월 26일'처럼 같은 말은 다시 쓰지 않는다
                     "level": "check" if why else "ok", "why": why}
    elif v.get("date_expr"):
        f["date"] = {"value": v["date_expr"], "sub": "", "level": "check", "why": "날짜 하나로 정해지지 않았어요"}
    else:
        f["date"] = {"value": "모름", "sub": "", "level": "missing", "why": "통화에서 날짜가 나오지 않았어요"}

    if v.get("time"):
        f["time"] = {"value": v["time"], "sub": "", "level": "ok"}
    elif v.get("time_expr"):
        f["time"] = {"value": v["time_expr"], "sub": "", "level": "check", "why": "시각 하나로 정해지지 않았어요"}
    else:
        f["time"] = {"value": "모름", "sub": "", "level": "missing", "why": "통화에서 진료 시각이 나오지 않았어요"}

    cands = h.get("candidates") or []
    if cands:
        sure = h.get("status") == "matched" and h.get("basis") != "department"
        why = None if sure else ("아픈 곳으로 고른 병원이에요" if h.get("basis") == "department"
                                 else f"후보 {len(cands)}곳 가운데 첫 번째예요")
        f["hospital"] = {"value": cands[0]["name"], "sub": "관외" if cands[0].get("far") else (h.get("department") or ""),
                         "level": "ok" if sure else "check", "why": why}
    else:
        f["hospital"] = {"value": "모름", "sub": h.get("department") or "", "level": "missing", "why": "통화에서 병원이 나오지 않았어요"}

    why_place = ("리를 몰라 희망택시를 탈 수 있는지 못 정했어요" if "village_town_only" in checks
                 else "등록된 주소로 정한 교통수단이에요" if "village_from_roster" in checks
                 else "등록된 주소와 마을이 달라 확인이 필요해요" if "village_mismatch" in checks else None)
    f["transport"] = _transport(req, why_place)

    need = (req.get("companion") or {}).get("needed")
    f["companion"] = {"value": {"yes": "병원동행", "no": "필요 없음"}.get(need, "모름"),
                      "sub": {"yes": "같이 가 드릴 분 필요", "no": "혼자 가실 수 있음"}.get(need, ""),
                      "level": "ok" if need in ("yes", "no") else "check",
                      "why": None if need in ("yes", "no") else "동행이 필요한지 듣지 못했어요"}

    if p.get("village"):
        why = ("읍·면까지만 들었어요" if "village_town_only" in checks
               else "등록된 주소로 채웠어요" if "village_from_roster" in checks
               else "등록된 주소와 달라요" if "village_mismatch" in checks
               else None if p.get("detail") else "모시러 갈 곳을 듣지 못했어요")
        f["place"] = {"value": form_address(p), "sub": "", "level": "check" if why else "ok", "why": why}
        if p.get("map"):  # 카카오맵에서 찾은 곳은 지도 링크도 준다
            f["place"]["url"] = p["map"].get("url")
            if p["map"].get("alts") and not why:
                f["place"].update(level="check", why=f"같은 리에 {', '.join(p['map']['alts'])}도 있어요. 어느 곳인지 확인이 필요해요")
    else:
        f["place"] = {"value": "모름", "sub": p.get("detail") or "", "level": "missing", "why": "통화에서 마을이 나오지 않았어요"}
    return f


def make_summary(req: dict, ai_summary: str | None) -> str:
    """목록에 보일 한 줄 요약. 병원 방문은 규칙으로 바로잡은 날짜와 고른 병원으로 만든다."""
    if req.get("intent") != "hospital_visit":
        return ai_summary or ""
    visit = req.get("visit") or {}
    hosp = req.get("hospital") or {}
    when = _date_label(visit["date"]) if visit.get("date") else (visit.get("date_expr") or "날짜 미정")
    if visit.get("time"):
        when += f" {visit['time']}"
    cands = hosp.get("candidates") or []
    where = cands[0]["name"] if hosp.get("status") == "matched" and cands else (
        hosp.get("query") or hosp.get("department") or "병원 미정")
    parts = [when, where]
    if (req.get("companion") or {}).get("needed") == "yes":
        parts.append("동행 필요")
    if req.get("wheelchair_needed") == "yes":
        parts.append("휠체어")
    return " · ".join(parts)


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
        "어르신": elder_name(req) or "확인 필요",
        "나이": f"{resident['age']}세" if resident.get("age") else "확인 필요",
        "연락처": (req.get("call") or {}).get("caller") or "확인 필요",
        "사는 마을": pickup.get("village") or "확인 필요",
        "상세 주소": pickup_address(pickup) or pickup.get("detail") or "확인 필요",
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
            "요금": COMPANION_FEE.get(resident.get("low_income"), PRICE["companion"]),
        }, deadline + ["대상은 홍천에 사는 65세 이상, 걸을 수 있는 분이에요. 차량은 없어서 교통비는 본인이 내요."]
           + (["휠체어를 타시면 병원동행 대상인지 노인복지관(033-430-8753)에 확인해야 해요."]
              if req.get("wheelchair_needed") == "yes" else [])))

    taxi = _hope_taxi_match(pickup.get("village"))
    if taxi:
        town = taxi["읍면"]
        notes = [f"확인된 운행 구역: {taxi['리']} {taxi['세부 마을·반'] or ''}".strip(),
                 "개인 등록제가 아니에요. 이장이 날짜·시간·장소를 미리 요청해요.",
                 "읍·면 소재지까지만 가요. 홍천읍 병원이면 소재지에서 버스로 갈아타요.",
                 {True: "교통복지카드가 있어 갈아탈 버스는 무료예요(월 20회).",
                  False: "교통복지카드가 없어요. 읍·면에서 신청하면 농어촌버스가 월 20회 무료예요."}.get(
                     resident.get("bus_card"), "65세 이상은 교통복지카드가 있으면 농어촌버스가 월 20회 무료예요.")]
        apps.append(_app("hope_taxi", len(apps) + 1, {
            "마을": pickup.get("village"),
            "승차 지점": pickup_address(pickup) or pickup.get("point") or "마을회관 (확인 필요)",
            "운행일": common["진료일"],
            "목적지": f"{town} 소재지" if town != "홍천읍" else "홍천읍내 병원",
            "인원": "1명" + (" + 동행 매니저" if companion.get("needed") == "yes" else ""),
            "왕복 여부": "왕복",
            "요금": PRICE["hope_taxi"],
        }, notes))

    if req.get("wheelchair_needed") == "yes":
        apps.append(_app("special_transport", len(apps) + 1, {
            **common,
            "출발지": pickup_address(pickup) or pickup.get("village") or "확인 필요",
            "목적지": common["병원"],
            "휠체어": "필요",
            "교통약자 등록": {True: "등록됨", False: "미등록"}.get(resident.get("special_transport"), "확인 필요"),
            "요금": PRICE["special_transport"],
        }, [{True: "등록된 분이라 바로 예약할 수 있어요.",
             False: "등록이 안 된 분이에요. 신청서·신분증·휠체어 이용 증빙을 내고 결과까지 최대 14일 걸려서, "
                    "진료일 전에 못 탈 수 있어요."}.get(
                resident.get("special_transport"),
                "미리 등록한 분만 탈 수 있어요. 등록 안 했으면 신청서·신분증·휠체어 이용 증빙을 내고, 결과까지 최대 14일 걸려요."),
            "예약은 7일 전부터 하루 전까지 광역이동지원센터(1577-2014)로 해요."]
           + (["관외 병원이에요. 노인 자격으로 등록한 분은 홍천 안에서만 탈 수 있어서 확인이 필요해요."]
              if any(c.get("far") for c in (req.get("hospital") or {}).get("candidates") or []) else [])))
    return apps


def refresh_status(req: dict) -> None:
    """기관별 신청서 상태로 신청 전체 상태를 정한다. 보류와 안내 완료는 그대로 둔다.

    안내문 초안은 기관이 모두 답했을 때 만든다. 기관 신청이 필요 없는 병원 방문은 바로 만든다.
    """
    if req.get("status") in ("held", "notified", "processing"):
        return
    apps = req.get("applications") or []
    states = {a["status"] for a in apps}
    if "draft" in states:
        req["status"] = "pending"
    elif "submitted" in states:
        req["status"] = "submitted"
    elif apps:
        req["status"] = "confirmed"
    else:
        req["status"] = "pending"
    ready = req["status"] == "confirmed" or (not apps and req.get("intent") == "hospital_visit")
    if ready and req.get("message") is None:
        req["message"] = {"status": "draft", "text": confirmation_text(req), "channel": "기록",
                          "history": [_event("draft", "안내문 자동 작성")]}


SHORT = {"companion": "병원동행", "hope_taxi": "희망택시", "special_transport": "특별교통수단"}


def _topic(word: str) -> str:
    """받침에 맞춰 은/는을 붙인다."""
    last = word[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + ("은" if has_final else "는")


def confirmation_text(req: dict) -> str:
    """기관 응답으로 어르신께 보낼 확정 안내문을 만든다. 표준어 존댓말, 짧게."""
    name = elder_name(req)
    visit = req.get("visit") or {}
    who = f"{name} 어르신" if name else "어르신"
    lines = [f"{who}, {_date_label(visit.get('date'))} {_hospital_label(req)} 가시는 날 안내드립니다."]
    for a in req.get("applications") or []:
        resp = a.get("response") or {}
        if a["status"] == "confirmed":
            fee = (a.get("fields") or {}).get("요금") or PRICE[a["type"]]
            lines.append(f"{SHORT[a['type']]}: {resp.get('detail') or '확정되었습니다.'} (요금: {fee})")
        elif a["status"] == "rejected":
            lines.append(f"{_topic(SHORT[a['type']])} 이번에는 어렵습니다. 담당자가 다시 연락드리겠습니다.")
    if not req.get("applications"):
        lines.append("말씀하신 내용 잘 받았습니다.")
    lines.append("전날 다시 연락드리겠습니다.")
    return "\n".join(lines)
