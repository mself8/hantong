from datetime import date

import pytest

from app.normalize import match_hospital, match_village, resolve_date, resolve_time

TODAY = date(2026, 11, 18)  # 수요일. 가상 발화 평가셋의 통화일과 같다.


@pytest.mark.parametrize("expr, expected", [
    ("담주 화요일", "2026-11-24"),
    ("다음 주 화요일", "2026-11-24"),
    ("모레 아침", "2026-11-20"),
    ("다음 달 3일 열 시 반", "2026-12-03"),
    ("금요일날", "2026-11-20"),
    ("25일 오후 두 시", "2026-11-25"),
    ("내일", "2026-11-19"),
    ("이번 주 토요일 오전", "2026-11-21"),
    ("다음 주 월요일 아홉 시", "2026-11-23"),
    ("오늘 오후", "2026-11-18"),
    ("12월 3일", "2026-12-03"),
    ("12월 둘째 주", None),
    ("다음 장날쯤", None),
])
def test_resolve_date(expr, expected):
    got = resolve_date(expr, TODAY)
    assert (got.isoformat() if got else None) == expected


@pytest.mark.parametrize("expr, expected", [
    ("열 시 반", "10:30"),
    ("오후 두 시", "14:00"),
    ("두 시", "14:00"),
    ("아홉 시", "09:00"),
    ("9시", "09:00"),
    ("오후 2시 30분", "14:30"),
    ("오전", None),
    ("한 시간 뒤에", None),
])
def test_resolve_time(expr, expected):
    assert resolve_time(expr) == expected


def names(r):
    return [c["name"] for c in r["candidates"]]


def test_hospital_alias():
    assert names(match_hospital("아산병원")) == ["홍천아산병원"]
    assert names(match_hospital("강대병원")) == ["강원대학교병원"]
    assert names(match_hospital("춘천 성심병원")) == ["한림대학교춘천성심병원"]


def test_hospital_ambiguous_is_not_guessed():
    r = match_hospital("춘천 큰 병원")
    assert r["status"] == "candidates" and set(names(r)) == {"강원대학교병원", "한림대학교춘천성심병원"}
    assert match_hospital("서울 아산병원")["status"] == "unknown"


def test_hospital_by_department():
    r = match_hospital("읍내 정형외과")
    assert r["status"] == "candidates"
    assert set(names(r)[:2]) == {"서울정형외과의원", "윤성일정형외과의원"}
    r = match_hospital("눈 보는 데")
    assert r["department"] == "안과" and {"서울안과의원", "홍천연세안과의원"} <= set(names(r))


def test_hospital_named_clinic():
    assert names(match_hospital("윤성일 정형외과")) == ["윤성일정형외과의원"]


def test_health_centers():
    r = match_hospital("보건소", home_town="서석면")
    assert r["status"] == "candidates" and names(r) == ["홍천군서석면보건지소", "홍천군보건소"]
    assert names(match_hospital("보건소")) == ["홍천군보건소"]
    assert names(match_hospital("진료소", home_village="수하리")) == ["홍천군서석면수하보건진료소"]


def test_village():
    assert match_village("수하 사는데").label == "서석면 수하리"
    assert match_village("서석면 풍암리").label == "서석면 풍암리"
    assert match_village(None) is None


def test_rules_found_by_eval_set():
    """가상 통화 평가 자료(v1)의 규칙 점검에서 찾은 경우들."""
    from datetime import date as d
    assert resolve_date("이틀 뒤에 가야 해요", d(2026, 12, 15)) == d(2026, 12, 17)
    assert resolve_date("하루 뒤", d(2026, 12, 31)) == d(2027, 1, 1)
    assert match_village("북방면 능평리에서 출발해요").label == "북방면 능평리"
    assert match_village("홍천읍 진리에서 출발해요").label == "홍천읍 진리"
    assert names(match_hospital("홍천한의원에서 한방내과 진료를 보려고요")) == ["홍천한의원"]
    assert names(match_hospital("서울삼성내과에서 내과 진료를 보려고요")) == ["서울삼성내과의원"]
    r = match_hospital("춘천성심병원인지 춘천 강원대병원인지 헷갈려요")
    assert r["status"] == "candidates" and set(names(r)) == {"한림대학교춘천성심병원", "강원대학교병원"}
    r = match_hospital("병원 이름은 잘 모르고 가정의학과에 가야 해요")
    assert r["basis"] in ("department", None) and not any("사단" in n for n in names(r))
