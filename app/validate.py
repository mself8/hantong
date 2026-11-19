"""신청서를 규칙으로 점검한다. AI 판단이 아니라 정해진 규칙과 공개 자료로만 확인한다."""
from __future__ import annotations

from datetime import date

from .data import WEEKDAYS, hospital_by_id, holidays

# 홍천군노인복지관 어르신 병원동행: 희망일 7일 전까지 전화 예약 (data/public/병원동행_운영기준.csv)
COMPANION_DAYS_AHEAD = 7


def _check(kind: str, level: str, message: str) -> dict:
    return {"type": kind, "level": level, "message": message}


def run_checks(req: dict, today: date) -> list[dict]:
    checks: list[dict] = []
    if req.get("intent") != "hospital_visit":
        return checks

    hosp = req.get("hospital") or {}
    visit = req.get("visit") or {}
    companion = req.get("companion") or {}
    matched = hosp.get("status") == "matched" and hosp.get("candidates")
    h = hospital_by_id().get(hosp["candidates"][0]["id"]) if matched else None

    if hosp.get("status") == "candidates":
        names = ", ".join(c["name"] for c in hosp.get("candidates", []))
        checks.append(_check("hospital_unclear", "warn", f"병원이 하나로 정해지지 않았어요. 후보: {names}"))
    elif not matched:
        checks.append(_check("hospital_unclear", "warn", "말씀하신 병원을 목록에서 찾지 못했어요. 어르신께 확인이 필요해요."))
    if h and h.far:
        checks.append(_check("far", "info", f"{h.sigungu} 병원이라 희망택시 범위 밖이에요. 다른 이동 수단을 봐야 해요."))

    d = visit.get("date")
    if not d:
        checks.append(_check("date_unclear", "warn", "진료 날짜를 정하지 못했어요. 어르신께 날짜를 확인해야 해요."))
        return checks
    day = date.fromisoformat(d)
    wd = WEEKDAYS[day.weekday()]
    if day < today:
        checks.append(_check("date_past", "warn", f"{d}는 이미 지난 날짜예요."))
    if d in holidays():
        checks.append(_check("holiday", "warn", f"{d}({wd})는 {holidays()[d]}이라 병원이 쉴 수 있어요."))
    elif wd == "일":
        checks.append(_check("sunday", "warn", f"{d}는 일요일이라 대부분 병원이 쉬어요."))

    if h:
        if not h.hours:
            checks.append(_check("hours_unknown", "info", f"{h.name} 진료시간은 공개 자료에 없어요. 전화({h.phone})로 확인해 주세요."))
        elif wd != "일" and wd not in h.hours:
            checks.append(_check("clinic_closed", "warn", f"{h.name}은 {wd}요일 진료 정보가 없어요. 쉬는 날일 수 있어요."))
        elif wd in h.hours and visit.get("time"):
            start, end = h.hours[wd]
            if not (start <= visit["time"] <= end):
                checks.append(_check("clinic_closed", "warn",
                                     f"{h.name} {wd}요일 진료시간은 {start}~{end}인데 {visit['time']}로 신청했어요."))

    if companion.get("needed") == "yes":
        days_left = (day - today).days
        if days_left < COMPANION_DAYS_AHEAD:
            checks.append(_check("companion_deadline", "warn",
                                 f"병원동행은 {COMPANION_DAYS_AHEAD}일 전까지 예약해야 하는데 {days_left}일 남았어요. "
                                 "노인복지관에 바로 확인해야 해요."))
        elif days_left == COMPANION_DAYS_AHEAD:
            checks.append(_check("companion_deadline", "info", "오늘이 병원동행 예약 마감일이에요. 오늘 노인복지관에 예약해야 해요."))
    return checks
