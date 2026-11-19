"""통화 중 확정값 저장(save_info). 받아 적기가 두 번 틀려도(어르신 말, AI 목소리) 신청서는 저장값으로 만든다."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main, pipeline, slots
from app.main import app as api
from tests.test_api import FIELDS, report

client = TestClient(api)
FRIDAY = datetime(2026, 11, 20, 9, 0)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    slots.STATE.clear()
    slots._DONE.clear()
    monkeypatch.setattr(slots, "now_kst", lambda: FRIDAY)
    monkeypatch.setattr(pipeline, "now_kst", lambda: FRIDAY)
    yield
    slots.STATE.clear()


def tool(call_id, **args):
    body = {"message": {"type": "tool-calls", "call": {"id": call_id}, "toolCallList": [
        {"id": "t1", "function": {"name": "save_info", "arguments": args}}]}}
    return client.post("/vapi/tools", json=body).json()["results"][0]["result"]


def test_saves_and_says_what_is_still_missing():
    m = tool("c1", name="최혜자")
    assert m.startswith("저장: 성함 최혜자") and "아직 모르는 것: 주소" in m
    tool("c1", address="서석면 청량1리 30번지")
    m = tool("c1", address="아니 청량2리 30번지")  # 고치시면 마지막 값이 남는다
    assert "청량2리(숫자 둘) 30번지" in m  # 전화로는 '이리'와 '일리'가 거의 같게 들린다
    assert slots.STATE["c1"]["address"]["detail"] == "청량2리 30번지"


def test_hospital_date_and_time_are_checked_by_rules():
    tool("c2", address="홍천읍 결운리 50번지")
    assert "병원 서울안과의원" in tool("c2", hospital="서울안과")
    m = tool("c2", date="다음 주 수요일", time="오전 열 시")
    assert "11월 25일 수요일" in m and "오전 10시" in m
    m = tool("c2", date="내일")
    assert "토요일이라" in m and "일주일 전에 예약" in m
    assert "8시" not in m and "닫았을 수" in tool("c2", time="아침 7시")
    assert "후보가 여럿" in tool("c3", hospital="안과")  # 이름 없이 진료과만 말하면 고르게 한다
    assert "없는" in tool("c4", address="홍천읍 상수리") or "목록에 없어요" in tool("c4", address="홍천읍 상수리")


def test_recap_comes_from_saved_values():
    tool("c5", name="박영자", address="서석면 수하리 123번지", hospital="서울의원", date="다음 주 수요일", time="10시")
    m = tool("c5", companion="아니요", bus="예")
    assert "다 들었어요" in m and "박영자 어르신, 서석면 수하리 123번지, 11월 25일 수요일 오전 10시 서울의원, 혼자 가심, 버스로 가심" in m
    m = tool("c6", companion="yes")
    assert "휠체어를 타시는지" in m and "모시러 갈 곳" in m


def test_confirmed_values_win_over_the_transcript_and_show_on_live():
    tool("call-1", name="최혜자", address="서석면 청량2리 30번지", hospital="서울안과의원",
         date="다음 주 수요일", time="오전 10시", companion="yes")
    body = report(dict(FIELDS, caller_name="최해자", village="청량1리", address_detail="청량1리 30번지"))
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    dump = json.dumps(req, ensure_ascii=False)
    assert req["pickup"]["detail"] == "청량2리 30번지" and req["visit"]["date"] == "2026-11-25"
    assert "최혜자" in dump and "최해자" not in dump and "서울안과의원" in dump
    assert "call-1" not in slots.STATE
    rows = main._live_view({"id": "call-1", "request_id": None})["slots"]
    assert {"label": "주소", "value": "서석면 청량2리 30번지"} in rows  # 통화 뒤에도 실시간 화면에 남는다


def test_dashboard_test_button():
    assert "성함 김순자" in client.post("/vapi/tools", json={"name": "김순자"}).json()["result"]
