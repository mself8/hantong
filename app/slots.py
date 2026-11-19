"""통화 중에 확정한 값을 서버에 적어 둔다(성함·주소·병원·날짜·시각·동행·휠체어·버스·모시러 갈 곳).

전화 음성은 두 번 받아 적힌다. 어르신 말을 글로 바꿀 때 한 번, AI 목소리를 기록으로 남길 때 또 한 번.
둘 다 틀릴 수 있어서('청량이리'↔'청량일리', '서울안과'→'서울항과') 대화 기록만 믿으면 한 번 틀린 글자가
대화와 신청서로 번진다(11-20 시험 통화). 그래서 AI가 알게 된 값은 바로 save_info 도구로 저장하고,
서버가 목록·규칙으로 확인해 되읽을 문장과 아직 모르는 칸을 돌려준다. 신청서는 이 저장값으로 만든다.
"""
from __future__ import annotations

import re
import threading
from datetime import date

from . import address
from .data import WEEKDAYS, holidays, hospital_by_id
from .normalize import match_hospital, resolve_date, resolve_time
from .pipeline import now_kst, unusual_surname

_LOCK = threading.Lock()
STATE: dict[str, dict] = {}  # 통화 id → 확정값. 통화가 끝나면 take()로 꺼내 신청서에 쓴다.

LABEL = {"name": "성함", "address": "주소", "hospital": "병원", "date": "날짜", "time": "진료 시각",
         "companion": "같이 가 드릴 분", "wheelchair": "휠체어", "bus": "버스", "pickup": "모시러 갈 곳"}
ASK = {"name": "성함", "address": "주소(읍·면, 리, 번지)", "hospital": "병원", "date": "가는 날짜", "time": "진료 시각",
       "companion": "같이 가 드릴 분이 필요한지", "wheelchair": "휠체어를 타시는지", "bus": "버스를 타실 수 있는지",
       "pickup": "모시러 갈 곳"}
speak_ri = address.speak_ri  # '청량2리' → '청량이리(숫자 둘)'


def speak_date(d: date) -> str:
    return f"{d.month}월 {d.day}일 {WEEKDAYS[d.weekday()]}요일"


def speak_time(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    part = "오전" if h < 12 else "낮" if h == 12 else "오후"
    hh = h if h <= 12 else h - 12
    return f"{part} {hh}시" + (" 반" if m == 30 else f" {m}분" if m else "")


def date_notes(d: date, today: date) -> list[str]:
    """날짜에 붙일 안내(지난 날·공휴일·주말·동행 7일 규칙). 날짜 도구와 같은 규칙."""
    wd, days, notes = WEEKDAYS[d.weekday()], (d - today).days, []
    if days < 0:
        notes.append("이미 지난 날이에요")
    if d.isoformat() in holidays():
        notes.append(f"공휴일({holidays()[d.isoformat()]})이라 쉬는 병원이 많아요")
    elif wd in ("토", "일"):
        notes.append(f"{wd}요일이라 쉬는 병원이 많아요")
    if 0 <= days < 7:
        notes.append("같이 가 드릴 분은 일주일 전에 예약해야 해서, 동행이 필요하면 그날은 어려울 수 있어요")
    return notes


def _yes_no(v) -> str | None:
    s = re.sub(r"\s+", "", str(v if v is not None else "")).lower()
    if not s:
        return None
    if s in ("no", "false", "아니요", "아니오", "아니", "불필요", "필요없음", "안함", "못탐"):
        return "no"
    if s in ("yes", "true", "예", "네", "응", "필요", "필요함", "탐"):
        return "yes"
    if any(w in s for w in ("아니", "없", "안 ", "못", "혼자")):
        return "no"
    if any(w in s for w in ("네", "예", "필요", "응")):
        return "yes"
    return None


def _hospital(st: dict, said: str, notes: list[str]) -> str | None:
    town = (st.get("address") or {}).get("village", "").split(" ")[0] or None
    m = match_hospital(said, None, town)
    if m["status"] == "matched":
        c = m["candidates"][0]
        st["hospital"] = {"name": c["name"], "id": c["id"], "far": c["far"]}
        if c["far"]:
            notes.append("홍천군 밖 병원이라 가는 방법은 담당자가 알아보고 알려 드린다고 하세요")
        return c["name"]
    if m["status"] == "candidates":
        names = ", ".join(c["name"] for c in m["candidates"][:3])
        notes.append(f"병원 후보가 여럿이에요({names}). 어느 곳인지 여쭤 보세요")
        return None
    st["hospital"] = {"name": said.strip(), "id": None, "far": None}
    notes.append(f"'{said.strip()}'은(는) 병원 목록에 없어 이름 그대로 적고 담당자가 확인해요")
    return said.strip()


def _hours_note(st: dict) -> str | None:
    """병원·날짜·시각이 다 있으면 공개 진료시간과 맞춰 본다. 자료에 없으면 지어내지 않고 그렇다고 한다."""
    h = hospital_by_id().get((st.get("hospital") or {}).get("id") or "")
    if not (h and st.get("date")):
        return None
    wd = WEEKDAYS[date.fromisoformat(st["date"]).weekday()]
    if not h.hours:
        return f"{h.name} 진료시간은 자료에 없어 담당자가 확인해요"
    if wd not in h.hours:
        return f"{h.name}은 {wd}요일 진료 정보가 없어요. 쉬는 날일 수 있어요"
    start, end = h.hours[wd]
    if st.get("time") and not (start <= st["time"] <= end):
        return f"{h.name} {wd}요일 진료시간은 {start}~{end}예요. 시각을 다시 여쭤 보세요"
    return None


def missing(st: dict) -> list[str]:
    """아직 모르는 칸(물어볼 순서대로)."""
    out = [k for k in ("name", "address", "hospital", "date", "time", "companion") if k not in st]
    if st.get("companion") == "yes" and "wheelchair" not in st:
        out.append("wheelchair")
    if st.get("wheelchair") != "yes" and "bus" not in st:
        out.append("bus")
    if (st.get("companion") == "yes" or st.get("wheelchair") == "yes" or st.get("bus") == "no") and "pickup" not in st:
        out.append("pickup")
    return out


def recap(st: dict) -> str:
    """마지막 확인 문장. AI가 기억이 아니라 저장값으로 되읽게 한다."""
    parts = [f"{st['name']} 어르신" if st.get("name") else None,
             speak_ri(st["address"]["label"]) if st.get("address") else None,
             " ".join(x for x in (speak_date(date.fromisoformat(st["date"])) if st.get("date") else None,
                                  speak_time(st["time"]) if st.get("time") else None,
                                  st["hospital"]["name"] if st.get("hospital") else None) if x) or None,
             {"yes": "같이 가 드릴 분 필요", "no": "혼자 가심"}.get(st.get("companion")),
             "휠체어 차량 필요" if st.get("wheelchair") == "yes" else None,
             "버스로 가심" if st.get("bus") == "yes" and st.get("wheelchair") != "yes" else None,
             f"{st['pickup']}에서 모심" if st.get("pickup") else None]
    return ", ".join(p for p in parts if p) + ". 이대로 신청할까요?"


def save(call_id: str | None, args: dict) -> str:
    """받은 칸을 확인해 저장하고, 되읽을 말 + 아직 모르는 것을 돌려준다."""
    today = now_kst().date()
    with _LOCK:
        st = STATE.setdefault(call_id or "-", {})
        said, notes = [], []
        if args.get("name"):
            name = re.sub(r"[\s.,]", "", str(args["name"]))
            st["name"] = name
            said.append(f"성함 {name}")
            if unusual_surname(name):
                notes.append(f"'{name[0]}' 씨는 흔하지 않은 성이에요. 한 번만 확인하세요")
        if args.get("address"):
            r = address.check(str(args["address"]))
            if r["status"] == "ok":
                st["address"] = {"village": r["village"], "detail": r["detail"], "label": r["label"]}
                said.append(f"주소 {speak_ri(r['label'])}")
                if not r["detail"]:
                    notes.append("번지는 아직 몰라요. 모르시면 리까지만 적어요")
            else:
                notes.append(r["message"].rstrip("."))
        if args.get("hospital"):
            name = _hospital(st, str(args["hospital"]), notes)
            if name:
                said.append(f"병원 {name}")
        if args.get("date"):
            d = resolve_date(str(args["date"]), today)
            if d:
                st["date"] = d.isoformat()
                said.append(f"날짜 {speak_date(d)}")
                notes += date_notes(d, today)
            else:
                notes.append(f"'{args['date']}'은(는) 날짜 하나로 정하지 못했어요. 며칠인지 여쭤 보세요")
        if args.get("time"):
            t = resolve_time(str(args["time"]))
            if t:
                st["time"] = t
                said.append(f"진료 시각 {speak_time(t)}")
                if not ("09:00" <= t <= "18:00"):
                    notes.append("그 시간엔 병원이 문을 닫았을 수 있어요")
            else:
                notes.append(f"'{args['time']}'은(는) 시각을 정하지 못했어요. 몇 시인지 여쭤 보세요")
        for k in ("companion", "wheelchair", "bus"):
            v = _yes_no(args.get(k))
            if v:
                st[k] = v
                said.append(f"{LABEL[k]} {'예' if v == 'yes' else '아니요'}")
        if args.get("pickup"):
            st["pickup"] = str(args["pickup"]).strip()
            said.append(f"모시러 갈 곳 {st['pickup']}")
        hours = _hours_note(st) if (args.get("hospital") or args.get("date") or args.get("time")) else None
        if hours:
            notes.append(hours)
        left = missing(st)
        msg = ("저장: " + ", ".join(said) + ". ") if said else ""
        msg += "".join(n + ". " for n in notes)
        if left:
            msg += "아직 모르는 것: " + ", ".join(ASK[k] for k in left) + ". 하나씩 여쭤 보세요."
        else:
            msg += "필요한 것을 다 들었어요. 이 문장으로 마지막 확인을 하세요: " + recap(st)
        return msg.strip()


def peek(call_id: str | None) -> list[dict]:
    """실시간 화면에 띄울 확정값(받아 적기가 틀려도 이 값이 맞다)."""
    with _LOCK:
        st = dict(STATE.get(call_id or "-") or _DONE.get(call_id or "-") or {})
    rows = []
    for k in ("name", "address", "hospital", "date", "time", "companion", "wheelchair", "bus", "pickup"):
        if k not in st:
            continue
        v = st[k]
        v = (v["label"] if k == "address" else v["name"] if k == "hospital" else
             speak_date(date.fromisoformat(v)) if k == "date" else speak_time(v) if k == "time" else
             {"yes": "예", "no": "아니요"}.get(v, v) if k in ("companion", "wheelchair", "bus") else v)
        rows.append({"label": LABEL[k], "value": v})
    return rows


def put_address(call_id: str | None, r: dict) -> None:
    """주소 확인 도구(check_address)가 맞다고 한 주소를 이 통화의 확정 주소로 적는다."""
    with _LOCK:
        STATE.setdefault(call_id or "-", {})["address"] = {"village": r["village"], "detail": r["detail"], "label": r["label"]}


_DONE: dict[str, dict] = {}  # 끝난 통화의 확정값(실시간 화면이 통화 뒤에도 보여 준다)


def take(call_id: str | None) -> dict | None:
    with _LOCK:
        st = STATE.pop(call_id or "-", None)
        if st and call_id:
            _DONE[call_id] = st
            while len(_DONE) > 50:
                _DONE.pop(next(iter(_DONE)))
        return st


def as_prefill(st: dict) -> dict:
    """저장값을 신청서 항목(Vapi 구조화 출력과 같은 이름)으로 바꾼다."""
    out = {}
    if st.get("name"):
        out["caller_name"] = st["name"]
    if st.get("address"):
        out.update(village=st["address"]["village"], address_detail=st["address"]["detail"])
    if st.get("hospital"):
        out["hospital_query"] = st["hospital"]["name"]
    if st.get("date"):
        d = date.fromisoformat(st["date"])
        out.update(date=st["date"], date_expr=f"{d.month}월 {d.day}일")
    if st.get("time"):
        out["time_expr"] = speak_time(st["time"]).replace(" 반", " 30분")
    for k, f in (("companion", "companion_needed"), ("wheelchair", "wheelchair_needed"), ("bus", "bus_ok")):
        if st.get(k):
            out[f] = st[k]
    return out
