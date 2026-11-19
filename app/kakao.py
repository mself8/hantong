"""카카오맵 장소 검색으로 어르신이 말한 모시러 갈 곳을 실제 주소로 바꾼다.

예: 서석면 수하리 + '마을회관 앞' → '강원특별자치도 홍천군 서석면 ○○길 12 (수하리마을회관) 앞'
- 키(Railway 변수 KAKAO_REST_KEY)가 없으면 아무것도 하지 않는다.
- 어르신이 사는 리 안에 있는 곳만 받는다. 못 찾거나 검색이 실패하면 None을 돌려주고 나머지 흐름은 그대로 간다.
- 카카오에는 마을 이름과 '마을회관' 같은 장소 말만 보낸다. 어르신 이름·전화번호는 보내지 않는다.
"""
from __future__ import annotations

import logging
import os
import re

import httpx
from urllib.parse import quote

from .normalize import match_village

URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
log = logging.getLogger("uvicorn.error")
_CACHE: dict[tuple, dict | None] = {}
# '마을회관 앞', '정류장 옆 파란 지붕 집', '경로당 근처예요' → 위치 말(앞·옆…)부터 끝까지는 찾을 곳이 아니라 꼬리
_TAIL = re.compile(r"(?:\s*(?:앞|옆|뒤|건너편|맞은편|근처|부근)|\s+쪽)(?=$|[\s에으이요예.,!?]).*$")  # '동쪽'의 쪽은 꼬리가 아니다
_END = re.compile(r"(?:이에요|예요|이요|요|[.,!?\s])+$")
_RI = re.compile(r"[가-힣]{2,4}리")
_LOT = re.compile(r"(산\s*)?\d+(-\d+)?(?=\s*번지|\s|$)")  # 번지: 123, 123-4, 산 12 ('수하2리'·'2층'의 숫자는 아니다)


def enabled() -> bool:
    return bool(os.environ.get("KAKAO_REST_KEY"))


def _search(params: dict, url: str = URL) -> list[dict]:
    r = httpx.get(url, params=params, timeout=3,
                  headers={"Authorization": f"KakaoAK {os.environ['KAKAO_REST_KEY']}"})
    r.raise_for_status()
    return r.json().get("documents") or []


def _find_lot(village, lot: str) -> dict | None:
    """번지로 집 주소를 찾는다(카카오 주소 검색). 도로명 주소가 있으면 도로명, 없으면 지번."""
    docs = [d for d in _search({"query": f"강원 홍천군 {village.town} {village.name} {lot}"}, ADDRESS_URL)
            if "홍천군" in d["address_name"] and village.name in d["address_name"]]
    if not docs:
        return None
    d = docs[0]
    jibun = (d.get("address") or {}).get("address_name") or d["address_name"]
    road = (d.get("road_address") or {}).get("address_name")
    return {"name": f"{village.name} {lot}" if road else "", "address": road or jibun,
            "url": f"https://map.kakao.com/link/map/{quote(jibun)},{d['y']},{d['x']}", "alts": []}


def split_place(text: str, village) -> tuple[str, str]:
    """'수하리 마을회관 앞' → ('마을회관', ' 앞'). 마을 이름은 검색할 때 따로 붙이므로 뺀다."""
    m = _TAIL.search(text)
    spot, tail = (text[:m.start()], m.group(0)) if m else (text, "")
    for w in (village.town, village.name):
        spot = spot.replace(w, " ")
    spot, tail = _END.sub("", " ".join(spot.split())), _END.sub("", tail).strip()
    return spot, (" " + tail if tail else "")


def find_pickup(text: str | None, village) -> dict | None:
    """모시러 갈 곳을 어르신이 사는 리 안에서 찾는다. 찾으면 {'name', 'address', 'url', 'tail'}."""
    if not (enabled() and text and village):
        return None
    spot, tail = split_place(text, village)
    lot = _LOT.search(spot or "")
    if not spot or (not lot and re.search(r"(집|댁|자택)$", spot)):
        return None  # 번지 없이 '집 앞'이면 지도에서 찾을 수 없다
    for w in _RI.findall(spot):  # 사는 곳은 수하리인데 '결운리 마을회관'처럼 다른 리를 말하면, 수하리 안에서 찾으면 틀린다
        other = match_village(w)
        if other and other.code != village.code:
            return None
    key = (spot, village.code)
    if key not in _CACHE and lot:  # 번지를 말씀하셨으면 주소 검색으로 집을 찾는다
        try:
            _CACHE[key] = _find_lot(village, re.sub(r"\s+", "", lot.group(0)))
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("카카오 주소 검색 실패: %s", e)
            return None
    if key not in _CACHE:
        near = {"x": village.lon, "y": village.lat}
        found = None
        try:
            for params in ({"query": f"홍천 {village.town} {village.name} {spot}", **near, "radius": 5000},
                           {"query": spot, **near, "radius": 3000, "sort": "distance"}):  # 그 리에서 가장 가까운 '마을회관'
                docs = [d for d in _search(params)
                        if "홍천군" in d["address_name"] and village.town in d["address_name"]
                        and village.name in d["address_name"]]
                if docs:
                    d, same = docs[0], spot.replace(" ", "")
                    found = {"name": d["place_name"], "address": d.get("road_address_name") or d["address_name"],
                             "url": (d.get("place_url") or "").replace("http://", "https://"),
                             # 수하1리·수하2리처럼 같은 리에 같은 이름의 곳이 더 있으면 어느 곳인지 확인해야 한다
                             "alts": [o["place_name"] for o in docs[1:3] if same in o["place_name"].replace(" ", "")]}
                    break
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("카카오맵 검색 실패: %s", e)
            return None
        _CACHE[key] = found
    found = _CACHE[key]
    return {**found, "tail": tail} if found else None
