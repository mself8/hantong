"""data/ 폴더의 병원·마을·주민·공휴일을 읽는다."""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _hhmm(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v.isdigit():
        return None
    v = v.zfill(4)
    return f"{v[:2]}:{v[2:]}"


def _float(v: str | None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Hospital:
    id: str
    name: str
    kind: str
    sigungu: str
    town: str
    address: str
    phone: str
    departments: list[str]
    hours: dict[str, list[str]]  # 요일 -> [시작, 종료]. 공개 자료에 없으면 그 요일 키가 없다.
    sunday_note: str
    holiday_note: str
    lat: float | None
    lon: float | None
    far: bool  # 홍천군 밖

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Village:
    code: str
    town: str  # 읍·면
    name: str  # 리
    lat: float | None
    lon: float | None
    population: int | None
    elderly: int | None
    elderly_ratio: float | None

    @property
    def label(self) -> str:
        return f"{self.town} {self.name}"

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


def _hospital(row: dict, hid: str, far: bool) -> Hospital:
    hours = {}
    for wd in WEEKDAYS[:6]:
        start = _hhmm(row.get(f"진료시작시간_{wd}요일"))
        end = _hhmm(row.get(f"진료종료시간_{wd}요일"))
        if start and end:
            hours[wd] = [start, end]
    depts = [d.strip() for d in (row.get("진료과목") or "").split(",") if d.strip()]
    return Hospital(
        id=hid,
        name=row["요양기관명"],
        kind=row.get("종별코드명", ""),
        sigungu=row.get("시군구코드명", ""),
        town=row.get("읍면동", ""),
        address=row.get("주소", ""),
        phone=row.get("전화번호", ""),
        departments=depts,
        hours=hours,
        sunday_note=row.get("휴진안내_일요일", "") or "",
        holiday_note=row.get("휴진안내_공휴일", "") or "",
        lat=_float(row.get("위도")),
        lon=_float(row.get("경도")),
        far=far,
    )


@lru_cache
def hospitals() -> list[Hospital]:
    """홍천군 의료기관 88곳(HC…)과 인근 종합병원 8곳(NB…). 약국은 목적지가 아니라 뺀다."""
    out = [_hospital(r, f"HC{i:03d}", False) for i, r in enumerate(_rows(DATA / "public/홍천군_의료기관.csv"), 1)]
    out += [_hospital(r, f"NB{i:03d}", True) for i, r in enumerate(_rows(DATA / "public/인근_종합병원.csv"), 1)]
    return out


@lru_cache
def hospital_by_id() -> dict[str, Hospital]:
    return {h.id: h for h in hospitals()}


@lru_cache
def villages() -> list[Village]:
    coords = {r["법정동코드"]: r for r in _rows(DATA / "public/홍천군_리좌표.csv")}
    pops = {r["법정동코드"]: r for r in _rows(DATA / "public/홍천군_리별_고령인구.csv")}
    out = []
    for r in _rows(DATA / "public/홍천군_리목록.csv"):
        c, p = coords.get(r["법정동코드"], {}), pops.get(r["법정동코드"], {})
        out.append(Village(
            code=r["법정동코드"],
            town=r["읍면동명"],
            name=r["리명"],
            lat=_float(c.get("위도")),
            lon=_float(c.get("경도")),
            population=int(p["총인구"]) if p.get("총인구") else None,
            elderly=int(p["65세이상"]) if p.get("65세이상") else None,
            elderly_ratio=_float(p.get("고령비율")),
        ))
    return out


@lru_cache
def residents() -> list[dict]:
    """팀이 만든 가상 어르신 명부."""
    return _rows(DATA / "synthetic/residents.csv")


@lru_cache
def holidays() -> dict[str, str]:
    """날짜(YYYY-MM-DD) -> 공휴일 이름."""
    return {r["날짜"]: r["명칭"] for r in _rows(DATA / "public/공휴일_2026_2027.csv")}


def voice_cards() -> list[dict]:
    """음성 시험 카드 18장(다연 v1 04_음성 + 정답 병원). 시험 통화 페이지와 채점에 쓴다."""
    return _rows(DATA / "synthetic/voice_cards_v1.csv")
