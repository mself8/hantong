"""통화 중 주소 확인: 어르신이 말한 주소를 홍천군 마을 목록과 맞춰 본다.

AI가 기억으로 주소를 다루면 '청량2리'를 '청량1리'로 베끼거나, 없는 리('상수리')나
다른 면의 리('홍천읍 능평리')를 그대로 받아 적는다(11-20 시험 통화). 그래서 확인은
서버가 하고, AI는 돌려받은 글대로 되읽는다.
"""
from __future__ import annotations

import re

from .data import villages
from .kakao import _LOT
from .normalize import _edit, _jamo, match_village, sounds

OUTSIDE = ("춘천", "원주", "강릉", "횡성", "인제", "양구", "평창", "가평", "양평", "서울", "인천", "경기", "부산", "대구", "대전", "광주")
_KNUM = {"일": "1", "이": "2", "삼": "3", "사": "4", "오": "5"}


def _eun(word: str) -> str:
    """받침이 있으면 '은', 없으면 '는'."""
    c = ord(word[-1]) - 0xAC00
    return "은" if 0 <= c < 11172 and c % 28 else "는"


def _town(s: str) -> str | None:
    return next((t for t in sorted({v.town for v in villages()}, key=len, reverse=True) if t in s), None)


def ri_number(s: str, village) -> str | None:
    """'청량2리'·'청량 2리'·'청량이리'에서 마을 번호(2)를 찾는다."""
    core = re.escape(village.name.removesuffix("리"))
    m = re.search(core + r"(\d)리", s) or re.search(core + r"([일이삼사오])리", s)
    return _KNUM.get(m.group(1), m.group(1)) if m else None


def _heard_ri(text: str) -> str | None:
    """원래 문장(띄어쓰기 그대로)에서 마지막으로 말한 '○○리'를 뽑는다. '우리' 같은 낱말은 뺀다."""
    words = [w for w in re.findall(r"[가-힣]+리", text) if w not in ("우리", "거리", "자리", "머리") and not w.endswith(("읍", "면"))]
    return words[-1] if words else None


def _similar(word: str, town: str | None) -> list:
    """소리가 가까운 리 세 곳(같은 읍·면을 앞에)."""
    return sorted(villages(), key=lambda v: (_edit(_jamo(sounds(word)), _jamo(sounds(v.name))), v.town != town))[:3]


def check(text: str | None) -> dict:
    """status: ok(맞음) · other_town(다른 면의 리) · unknown(없는 리) · town_only · outside · none.
    message는 AI가 그대로 따라 할 수 있는 짧은 안내다."""
    s = re.sub(r"\s+", "", text or "")
    out = {"status": "none", "label": None, "village": None, "ri": None, "lot": None, "detail": None}
    if not s:
        out["message"] = "주소를 못 들었어요. 읍·면과 리, 번지를 여쭤 주세요."
        return out
    town = _town(s)
    v = match_village(s)
    if v is None:
        place = next((w for w in OUTSIDE if w in s), None)
        if place and not town:
            out.update(status="outside", message=f"{place}{_eun(place)} 홍천군 밖이에요. 잘못 들었을 수 있으니 한 번만 다시 여쭤 보고, "
                                                 "그래도 홍천군 밖이면 홍천군 어르신만 도와드린다고 안내하세요.")
            return out
        word = _heard_ri(text or "")
        if word:
            names = ", ".join(f"{x.name}({x.town})" for x in _similar(word, town))
            out.update(status="unknown", message=f"{town or '홍천군'}에 '{word}'{_eun(word)} 없어요. "
                                                  f"소리가 비슷한 리: {names}. 어느 리인지 다시 여쭤 보세요.")
            return out
        if town:
            out.update(status="town_only", message=f"{town}까지 들었어요. 무슨 리인지 여쭤 보세요.")
            return out
        out["message"] = "주소를 알아듣지 못했어요. 읍·면과 리를 다시 여쭤 주세요."
        return out
    core = v.name.removesuffix("리")
    if v.name not in s and not (len(core) >= 2 and core in s):  # 소리가 비슷한 리로 맞춘 경우(겨룬리 → 결운리, 상수리 → 성수리)
        heard = _heard_ri(text or "") or (text or "").strip()
        out.update(status="unknown", message=f"'{heard}'{_eun(heard)} 홍천군 마을 목록에 없어요. 가장 비슷한 리는 {v.town} {v.name}예요. "
                                             f"'{v.town} {v.name} 말씀이세요?'라고 여쭤 보세요.")
        return out
    if town and town != v.town:
        out.update(status="other_town", message=f"{v.name}{_eun(v.name)} {town}이 아니라 {v.town}에 있어요. "
                                                f"'{v.town} {v.name} 맞으세요?'라고 여쭤 보세요.")
        return out
    num = ri_number(s, v)
    ri = f"{v.name.removesuffix('리')}{num}리" if num else v.name
    lot = _LOT.search(s)
    lot_s = re.sub(r"\s+", "", lot.group(0)) if lot else None
    detail = f"{ri} {lot_s}번지" if lot_s else None
    label = f"{v.town} {detail or ri}"
    msg = f"확인: {label}. 이대로 되읽어 확인하세요."
    if not lot_s:
        msg += " 번지를 여쭤 보세요. 모르시면 리까지만 적어요."
    out.update(status="ok", label=label, village=f"{v.town} {ri}", ri=ri, lot=lot_s, detail=detail, message=msg)
    return out
