"""어르신이 말한 날짜·시간·병원·마을 표현을 실제 값과 목록에 맞춘다.

병원과 마을은 data/의 실제 목록 안에서만 고른다. 목록에 없으면 지어내지 않고 '확인 필요'로 남긴다.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .data import Hospital, Village, hospitals, villages

WD = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
KOR_HOUR = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
            "열": 10, "열한": 11, "열두": 12}


def _add_month(d: date, months: int, day: int) -> date | None:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    if day > calendar.monthrange(y, m)[1]:
        return None
    return date(y, m, day)


AFTER_DAYS = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "엿새": 6, "이레": 7, "일주일": 7}


def resolve_date(expr: str | None, today: date) -> date | None:
    """'담주 화요일', '모레', '25일', '다음 달 3일' 같은 표현을 날짜로 바꾼다. 범위나 애매한 표현은 None."""
    s = re.sub(r"\s+", "", expr or "")
    if not s:
        return None
    if "오늘" in s:
        return today
    if "글피" in s:
        return today + timedelta(days=3)
    if "모레" in s:
        return today + timedelta(days=2)
    if "내일" in s:
        return today + timedelta(days=1)
    m = re.search(r"(하루|이틀|사흘|나흘|닷새|엿새|이레|일주일)(뒤|후|있다가)", s)
    if m:
        return today + timedelta(days=AFTER_DAYS[m.group(1)])
    m = re.search(r"(\d{1,2})일(뒤|후)", s)
    if m:
        return today + timedelta(days=int(m.group(1)))

    m = re.search(r"(\d{1,2})월(\d{1,2})일", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year if (month, day) >= (today.month, today.day) else today.year + 1
        try:
            return date(year, month, day)
        except ValueError:
            return None

    m = re.search(r"(다음달|담달|다음다음달)(\d{1,2})일", s)
    if m:
        return _add_month(today, 2 if m.group(1) == "다음다음달" else 1, int(m.group(2)))

    m = re.search(r"([월화수목금토일])(요일|욜)", s)
    if m:
        wd = WD[m.group(1)]
        monday = today - timedelta(days=today.weekday())
        if re.search(r"다다음주|다담주", s):
            return monday + timedelta(days=14 + wd)
        if re.search(r"다음주|담주|내주|다음쭈|담쭈", s):
            return monday + timedelta(days=7 + wd)
        if re.search(r"이번주|금주|요번주|이번쭈", s):
            return monday + timedelta(days=wd)
        delta = (wd - today.weekday()) % 7 or 7  # 그냥 '금요일'은 오늘 다음에 오는 금요일
        return today + timedelta(days=delta)

    if re.search(r"(\d{1,2})주", s):  # '둘째 주' 같은 범위는 날짜 하나로 정하지 않는다
        return None
    m = re.search(r"(\d{1,2})일", s)
    if m:
        day = int(m.group(1))
        if day >= today.day:
            return _add_month(today, 0, day)
        return _add_month(today, 1, day)
    return None


def resolve_time(expr: str | None) -> str | None:
    """'열 시 반', '오후 두 시', '9시' 같은 표현을 HH:MM으로. 시가 없으면 None."""
    s = re.sub(r"\s+", "", expr or "")
    if not s:
        return None
    pm = any(k in s for k in ("오후", "저녁", "밤"))
    am = any(k in s for k in ("오전", "아침", "새벽"))
    m = re.search(r"(\d{1,2})시(?!간)(반|(\d{1,2})분)?", s)
    if m:
        hour = int(m.group(1))
        minute = 30 if m.group(2) == "반" else int(m.group(3) or 0)
    else:
        m = re.search(r"(열두|열한|열|한|두|세|네|다섯|여섯|일곱|여덟|아홉)시(?!간)(반)?", s)
        if not m:
            return None
        hour, minute = KOR_HOUR[m.group(1)], 30 if m.group(2) else 0
    if pm and hour < 12:
        hour += 12
    elif not am and not pm and 1 <= hour <= 6:  # 병원 시간대라 '두 시'는 오후로 본다
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


# ── 병원 ───────────────────────────────────────────────

# 어르신이 부르는 별명 -> 정확한 기관명. 긴 별명부터 맞춘다.
ALIASES = {
    "강릉아산": "강릉아산병원",
    "아산": "홍천아산병원",
    "춘천성심": "한림대학교춘천성심병원",
    "성심병원": "한림대학교춘천성심병원",
    "한림대": "한림대학교춘천성심병원",
    "강원대": "강원대학교병원",
    "강대": "강원대학교병원",
    "세브란스": "연세대학교 원주세브란스기독병원",
    "원주기독": "연세대학교 원주세브란스기독병원",
    "원주의료원": "강원특별자치도원주의료원",
    "강릉의료원": "강원특별자치도강릉의료원",
    "동인병원": "의료법인 강릉동인병원",
    "고려병원": "의산의료재단 강릉고려병원",
    "국군병원": "국군홍천병원",
    "보건의료원": "홍천군보건소",
}

# 아픈 곳·진료 이름 -> 진료과
DEPT_WORDS = {
    "안과": ["안과", "눈"],
    "치과": ["치과", "이빨", "이가", "틀니", "잇몸"],
    "정형외과": ["정형", "뼈", "무릎", "허리", "관절", "어깨"],
    "이비인후과": ["이비인후", "귀", "코가", "목이"],
    "한의원": ["한의원", "한방", "침맞", "침 맞", "침놓"],
    "비뇨의학과": ["비뇨"],
    "신경외과": ["신경외과"],
    "산부인과": ["산부인과"],
    "피부과": ["피부"],
    "가정의학과": ["가정의학"],
    "마취통증의학과": ["통증"],
    "내과": ["내과", "혈압", "당뇨", "속이", "위장", "감기", "배가", "배 아", "배아", "복통", "소화"],
}

CITY_WORDS = {"춘천": "춘천시", "원주": "원주시", "강릉": "강릉시"}
SUFFIXES = ("치과의원", "한의원", "의원", "요양병원", "병원")


def _core(name: str) -> str:
    core = name.replace("의료법인", "").replace(" ", "")
    for suf in SUFFIXES:
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    return core.removeprefix("홍천")


def detect_department(*texts: str | None) -> str | None:
    s = " ".join(t for t in texts if t)
    for dept, words in DEPT_WORDS.items():
        if any(w in s for w in words):
            return dept
    return None


def _has_dept(h: Hospital, dept: str) -> bool:
    if dept == "한의원":
        return h.kind == "한의원"
    if dept == "치과":
        return h.kind == "치과의원" or any("치과" in d or "치" == d[:1] for d in h.departments)
    return any(dept in d for d in h.departments) or dept.removesuffix("과") in h.name


SEOUL_BIG = ("아산", "삼성", "대병원", "성모", "세브란스", "큰병원")
CLOSED_TO_PUBLIC = ("사단", "의무대", "부대", "교도")  # 군부대·교정시설 의원은 주민이 못 간다


def _name_variants(h: Hospital) -> set[str]:
    """어르신이 기관을 부를 만한 이름. '홍천치과의원' → '홍천치과의원', '홍천치과'."""
    n = h.name.replace(" ", "").replace("의료법인", "")
    out = {n}
    for suf in SUFFIXES:
        if n.endswith(suf):
            base = n[: -len(suf)]
            out.add(base + {"치과의원": "치과", "한의원": "한의원"}.get(suf, ""))
            break
    return {v for v in out if len(v) >= 3 and v not in ("홍천", "치과", "한의원")}


def match_hospital(query: str | None, dept: str | None = None, home_town: str | None = None,
                   home_village: str | None = None) -> dict:
    """병원 표현을 실제 목록에 맞춘다.

    status: matched(한 곳) / candidates(여러 곳) / unknown(못 찾음)
    """
    q = re.sub(r"\s+", "", query or "")
    by_name = {h.name: h for h in hospitals()}
    dept = dept or detect_department(query)

    def result(status: str, found: list[Hospital], basis: str = "name") -> dict:
        # basis: name(기관 이름·별명을 말함) / department(이름 없이 진료과로 고름) / place(보건소·도시로 고름)
        return {"query": query, "department": dept, "status": status, "basis": basis if found else None,
                "candidates": [{"id": h.id, "name": h.name, "kind": h.kind, "town": h.town or h.sigungu,
                                "far": h.far} for h in found[:5]]}

    if not q and not dept:
        return result("unknown", [])

    # 1) 기관 이름이나 별명을 말한 경우. 두 곳 이상 말하면("A인지 B인지") 후보로 둔다.
    spans: list[tuple[int, int, Hospital]] = []
    for h in hospitals():
        for v in _name_variants(h):
            i = q.find(v)
            if i >= 0:
                spans.append((i, i + len(v), h))
    seoul = "서울" in q
    for alias, target in ALIASES.items():
        if seoul and any(k in alias for k in SEOUL_BIG):  # '서울 아산병원'은 홍천아산병원이 아니다
            continue
        i = q.find(alias)
        if i >= 0:
            spans.append((i, i + len(alias), by_name[target]))
    # 더 긴 이름 안에 든 짧은 이름은 버린다('홍천치과의원' 안의 '치과' 같은 것)
    spans = [a for a in spans if not any(b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0])
                                         for b in spans)]
    found = list({h.id: h for _, _, h in sorted(spans, key=lambda x: x[0])}.values())
    if found:
        return result("matched" if len(found) == 1 else "candidates", found)
    if "서울" in q and any(k in q for k in SEOUL_BIG):  # 서울 큰 병원은 목록 밖이다
        return result("unknown", [])
    named = [h for h in hospitals() if len(_core(h.name)) >= 2 and _core(h.name) in q]
    if named:
        named.sort(key=lambda h: len(_core(h.name)), reverse=True)
        best = [h for h in named if len(_core(h.name)) == len(_core(named[0].name))]
        return result("matched" if len(best) == 1 else "candidates", best)

    # 2) 보건소·보건지소·보건진료소
    if "진료소" in q:
        home_core = (home_village or "").replace(" ", "").removesuffix("리")[-2:]
        found = [h for h in hospitals() if h.kind == "보건진료소"
                 and (h.name.removesuffix("보건진료소")[-2:] in q
                      or (home_core and h.name.removesuffix("보건진료소").endswith(home_core)))]
        return result("matched" if len(found) == 1 else ("candidates" if found else "unknown"), found, "place")
    home_branch = [h for h in hospitals() if h.kind == "보건지소" and home_town and home_town in h.name]
    if "보건지소" in q and home_branch:
        return result("matched", home_branch, "place")
    if "보건소" in q:
        if home_branch and "읍내" not in q:  # 면에 사는 어르신의 '보건소'는 면 보건지소일 수 있다
            return result("candidates", home_branch + [by_name["홍천군보건소"]], "place")
        return result("matched", [by_name["홍천군보건소"]], "place")

    # 3) 진료과와 지역으로 좁히기
    city = next((v for k, v in CITY_WORDS.items() if k in q), None)
    if city:
        pool = [h for h in hospitals() if h.far and h.sigungu == city]
        if dept:
            pool = [h for h in pool if _has_dept(h, dept)] or pool
        return result("matched" if len(pool) == 1 else ("candidates" if pool else "unknown"), pool, "place")

    if not dept:
        return result("unknown", [])
    pool = [h for h in hospitals() if not h.far and h.kind in ("의원", "병원", "종합병원", "한의원", "치과의원")
            and not any(w in h.name for w in CLOSED_TO_PUBLIC)]
    town = "홍천읍" if ("읍내" in q or not home_town) else home_town
    local = [h for h in pool if h.town == town and _has_dept(h, dept)]
    if not local:
        local = [h for h in pool if h.town == "홍천읍" and _has_dept(h, dept)]
    # 진료과가 이름에 들어간 전문 의원이 있으면 그곳만 후보로 둔다
    specialty = [h for h in local if dept in h.name]
    local = sorted(specialty or local, key=lambda h: h.name)
    if not local:
        return result("unknown", [])
    return result("matched" if len(local) == 1 else "candidates", local, "department")


# ── 마을 ───────────────────────────────────────────────

# 받침 → 다음 글자 첫소리(홑받침만). ㅎ 받침은 'ㅇ' 앞에서 소리가 사라진다(-1).
_JONG_TO_CHO = {1: 0, 2: 1, 4: 2, 7: 3, 8: 5, 16: 6, 17: 7, 19: 9, 20: 10, 22: 12, 23: 14, 24: 15, 25: 16, 26: 17, 27: -1}


def sounds(s: str) -> str:
    """받침이 'ㅇ'으로 시작하는 다음 글자로 넘어가는 소리(연음)로 바꾼다. '결운리' → '겨룬리'.
    어르신이 말한 마을 이름을 음성 인식이 소리 나는 대로 적은 경우를 맞추려고 쓴다."""
    out = list(s)
    for i in range(len(out) - 1):
        a, b = ord(out[i]) - 0xAC00, ord(out[i + 1]) - 0xAC00
        if not (0 <= a < 11172 and 0 <= b < 11172) or b // 588 != 11:
            continue
        cho = _JONG_TO_CHO.get(a % 28)
        if cho is None:
            continue
        out[i] = chr(0xAC00 + a - a % 28)
        if cho >= 0:
            out[i + 1] = chr(0xAC00 + cho * 588 + b % 588)
    return "".join(out)


def match_village(text: str | None) -> Village | None:
    """'수하 사는데', '서석면 수하리' 같은 표현에서 리를 찾는다."""
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return None
    vs = villages()
    # 1) 리 이름을 그대로 말한 경우. 긴 이름 안에 든 짧은 이름('능평리' 안의 '평리')은 버린다.
    full = [v for v in vs if v.name in s]
    full = [v for v in full if not any(o.name != v.name and v.name in o.name for o in full)]
    if full:
        in_town = [v for v in full if v.town in s]
        return max(in_town or full, key=lambda v: len(v.name))
    # 2) 줄여 말한 경우('수하 사는데'). 읍·면 이름 속 글자('북방면'의 '북방')로는 맞추지 않는다.
    t = s
    for town in sorted({v.town for v in vs}, key=len, reverse=True):
        t = t.replace(town, " ")
    best = None
    for v in vs:
        core = v.name.removesuffix("리")
        if len(core) >= 2 and core in t:
            if best is None or len(core) > len(best.name.removesuffix("리")):
                best = v
    if best:
        return best
    # 3) 소리 나는 대로 적은 경우('결운리'를 '겨룬리'로). 글자로 못 찾았을 때만.
    st = sounds(t)
    heard = [v for v in vs if len(v.name.removesuffix("리")) >= 2 and sounds(v.name.removesuffix("리")) in st]
    if heard:
        return max(heard, key=lambda v: len(v.name))
    # 4) 받아 적기가 자모 하나 틀린 경우('결운리'를 '겨룻리'로). 가장 가까운 리가 하나뿐일 때만.
    for w in re.findall(r"[가-힣]{2,4}리", t):
        dist = [(v, _edit(_jamo(sounds(w)), _jamo(sounds(v.name)))) for v in vs if abs(len(v.name) - len(w)) <= 1]
        best = min(d for _, d in dist)
        close = [v for v, d in dist if d == best and d <= 1]
        close = [v for v in close if v.town in s] or close
        if len(close) == 1:
            return close[0]
    return None


def _jamo(s: str) -> list[int]:
    """글자를 첫소리·가운뎃소리·받침으로 쪼갠다(받침 없음도 한 칸)."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        out += [c // 588, 19 + (c % 588) // 28, 40 + c % 28] if 0 <= c < 11172 else [100 + ord(ch)]
    return out


def _edit(a: list[int], b: list[int]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]
