"""홍천 농어촌버스 안내: 마을에서 가까운 정류장, 다니는 노선, 홍천 가는 시각과 돌아오는 시각, 요금.

자료(모두 data/public, 출처는 SOURCES.md):
- 버스정류장.csv: 국토교통부 전국 버스정류장 위치정보(홍천 1,125곳)
- 농어촌버스_시간표.csv: 홍천군대중교통정보 공식 시간표
- 공공형버스_홍천읍-내면.csv, 농어촌버스_요금.csv: 같은 곳의 공지·요금 안내
- 서석면_마을별_버스.csv: 위 자료와 751번 경유정류장으로 팀이 정리한 서석면 마을별 버스
- 751번_경유정류장.csv: 홍천터미널에서 화촌·내촌을 거쳐 서석까지 가는 751번의 정류장 순서
시간표에는 기점과 종점 쪽 출발 시각만 있다. 마을 정류장을 지나는 시각은 없어서 '조금 뒤에 지나간다'고만 말한다.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from .data import DATA, _rows

_TIME_COL = "종점 쪽 시각(원문 '운행시간')"


def meters(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """두 좌표 사이 거리(미터)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return int(6371000 * 2 * math.asin(math.sqrt(a)))


def _times(values) -> list[str]:
    return sorted({v.strip() for v in values if ":" in (v or "")})


@lru_cache
def stops() -> list[tuple[str, float, float]]:
    return [(r["정류장명"], float(r["위도"]), float(r["경도"])) for r in _rows(DATA / "public/버스정류장.csv")]


@lru_cache
def segments() -> list[dict]:
    """시간표를 노선 구간(예: '홍천 ↔ 서석')별로 묶는다."""
    out: dict[tuple, dict] = {}
    for r in _rows(DATA / "public/농어촌버스_시간표.csv"):
        key = (r["권역(시간표 페이지)"], r["노선 구간"])
        s = out.setdefault(key, {"name": r["노선 구간"].replace("↔", " ↔ ").replace("  ", " ").strip(),
                                 "origin": r["기점"], "rows": [], "text": r["노선 구간"]})  # 권역 이름은 여러 지역을 묶어서 찾는 데 쓰지 않는다
        s["rows"].append(r)
        s["text"] += r["종점"] + r["경유지(순환)"] + r["비고·노선번호"]
    for s in out.values():
        s["from_origin"] = _times(r["기점 출발"] for r in s["rows"])
        s["from_end"] = _times(r[_TIME_COL] for r in s["rows"])
        sides = [re.sub(r"\s*\(.*?\)", "", x).strip() for x in s["name"].split("↔")]
        s["end"] = sides[0] if len(sides) == 2 and sides[1] == s["origin"] else sides[-1]  # '서석 ↔ 내면'인데 기점이 내면인 구간
        s["guess"] = any("추정" in r["해석"] for r in s["rows"])  # 종점 쪽 시각이 출발인지 도착인지 원문에 없다
    return list(out.values())


@lru_cache
def seoseok() -> dict[str, dict]:
    return {r["리"]: r for r in _rows(DATA / "public/서석면_마을별_버스.csv")}


@lru_cache
def route_751() -> list[str]:
    return [r["정류장명"] for r in _rows(DATA / "public/751번_경유정류장.csv")]


@lru_cache
def public_bus() -> str:
    rows = _rows(DATA / "public/공공형버스_홍천읍-내면.csv")
    go = "·".join(r["홍천 출발"] for r in rows if r["홍천 출발"])
    back = "·".join(f"{r['내면 출발']}(서석 {r['서석 경유']})" for r in rows if r["내면 출발"])
    return f"공공형버스 홍천↔내면(서석 경유): 홍천 출발 {go} / 내면 출발 {back}"


@lru_cache
def fare() -> str:
    rows = {r["구분"]: r for r in _rows(DATA / "public/농어촌버스_요금.csv")}
    return (f"요금은 현금 {rows['현금']['일반']}원, 교통카드 {rows['교통카드']['일반']}원. "
            "65세 이상은 교통복지카드가 있으면 농어촌버스가 월 20회 무료")


def nearest_stops(village, k: int = 2) -> list[tuple[str, int]]:
    """리 중심에서 가까운 정류장 k곳(이름이 같은 정류장은 한 번만). 마을 이름이 든 정류장(수하1리회관)을 먼저 고른다
    — 리 중심 좌표가 마을 정류장과 몇 km 떨어진 곳도 있다."""
    if village.lat is None or village.lon is None:
        return []
    core = village.name.removesuffix("리")
    near = [(name, lat, lon, meters(village.lat, village.lon, lat, lon)) for name, lat, lon in stops()]
    found: list[tuple[str, int]] = []
    for name, lat, lon, _d in sorted(near, key=lambda s: (not (len(core) >= 2 and core in s[0] and s[3] < 5000), s[3])):
        if name not in [n for n, _ in found]:
            found.append((name, meters(village.lat, village.lon, lat, lon)))
        if len(found) == k:
            break
    return found


def routes(village) -> list[dict]:
    """이 리를 지나는 노선 구간. 리 이름으로 먼저 찾고, 없으면 면 이름으로 찾는다."""
    core = village.name.removesuffix("리")
    hits = [s for s in segments() if len(core) >= 2 and core in s["text"]]
    if not hits:
        town = village.town.removesuffix("면") if village.town.endswith("면") and len(village.town) > 2 else village.town
        hits = [s for s in segments() if town != "홍천읍" and town in s["text"]]
    hits.sort(key=lambda s: (s["origin"] != "홍천", -len(s["rows"])))  # 홍천에서 오가는 노선, 자주 다니는 노선 먼저
    return hits[:2]


def _hm(name: str, m: int, core: str) -> str:
    if len(core) >= 2 and core in name:  # 마을 이름 정류장은 거리를 붙이지 않는다(리 중심 좌표가 어긋날 수 있다)
        return name
    return f"{name}(약 {round(m, -2) if m >= 100 else m}m)" if m < 1000 else f"{name}(약 {m / 1000:.1f}km)"


def guide_text(village) -> str:
    """통화 중 AI에게 돌려줄 버스 안내. AI는 이 가운데 필요한 한두 가지만 말한다."""
    parts = [f"{village.label} 버스 안내(홍천군대중교통정보 공식 시간표 기준)."]
    near = nearest_stops(village)
    if near:
        core = village.name.removesuffix("리")
        parts.append("가까운 정류장: " + ", ".join(_hm(n, m, core) for n, m in near) + ".")
    if village.town == "홍천읍":
        parts.append("홍천읍 안이에요. 가까운 정류장에서 홍천터미널(읍내) 방면 버스를 타시면 돼요.")
    on_751 = [n for n in route_751() if len(village.name) >= 3 and village.name.removesuffix("리") in n]
    if on_751:
        parts.append(f"751번(홍천터미널↔서석, 화촌·내촌 경유)이 '{on_751[0]}' 정류장을 지나요.")
    curated = seoseok().get(village.name) if village.town == "서석면" else None
    found = routes(village)
    if curated and curated["버스 이용 방법"] != "확인 필요":
        parts.append(f"타는 버스: {curated['버스 이용 방법']}. 시각: {curated['운행 시각(공식 시간표 기준)']}.")
    else:
        for s in found:
            times = []
            if s["from_end"]:
                times.append(f"{s['end']} 쪽 출발{'(추정)' if s['guess'] else ''} {'·'.join(s['from_end'])}")
            if s["from_origin"]:
                times.append(f"{s['origin']} 출발 {'·'.join(s['from_origin'])}")
            parts.append(f"노선 {s['name']}: " + " / ".join(times) + ".")
        if not found and village.town != "홍천읍":
            parts.append("이 마을을 지나는 노선은 시간표에서 찾지 못했어요(담당자 확인 필요).")
    if village.town in ("서석면", "내면"):
        parts.append(public_bus() + ".")
    parts.append("시간표에는 출발 시각만 있어서, 마을 정류장은 그보다 조금 뒤에 지나가요. " + fare() + ".")
    return " ".join(parts)
