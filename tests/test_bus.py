"""버스 안내: 공식 시간표·정류장 자료로 마을별 안내를 만들고, 통화 중 도구로 돌려준다."""
from fastapi.testclient import TestClient

from app import bus
from app.data import villages
from app.main import app as api
from tests.test_api import FIELDS, report

client = TestClient(api)
V = {v.label: v for v in villages()}


def test_village_stops_first_and_seoseok_times():
    t = bus.guide_text(V["서석면 수하리"])
    stops = t.split("가까운 정류장:")[1].split(".")[0]
    assert "수하" in stops                      # 리 중심 좌표가 어긋나도 마을 이름 정류장을 먼저
    assert "06:20" in t and "751번" in t and "1,700원" in t


def test_direction_labels_and_sorted_times():
    t = bus.guide_text(V["내면 창촌리"])
    assert "서석 쪽 출발" in t and "공공형.농어촌) 쪽" not in t   # 기점이 내면인 '서석 ↔ 내면' 구간
    seg = next(s for s in bus.segments() if s["name"].startswith("홍천 ↔ 양덕원"))
    assert seg["from_end"] == sorted(seg["from_end"])


def test_tool_call_answers_in_vapi_format():
    body = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "c1", "type": "function", "function": {"name": "bus_guide", "arguments": {"village": "서석면 수하리"}}},
        {"id": "c2", "type": "function", "function": {"name": "bus_guide", "arguments": "{\"village\": \"춘천\"}"}},
        {"id": "c3", "type": "function", "function": {"name": "weather", "arguments": {}}},
    ]}}
    r = client.post("/vapi/tools", json=body).json()["results"]
    assert r[0]["toolCallId"] == "c1" and "수하" in r[0]["result"]
    assert r[1]["toolCallId"] == "c2" and "찾지 못했어요" in r[1]["result"]
    assert "error" in r[2]


def test_bus_tile_shows_stop_and_far_hospital_needs_check():
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, bus_ok="yes"))).json()["id"]
    t = client.get(f"/api/requests/{rid}").json()["facts"]["transport"]
    assert t["value"] == "농어촌버스" and "정류장" in t["sub"]
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, hospital_query="춘천 강원대학교병원"))).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["facts"]["hospital"]["sub"] == "관외"
    assert req["facts"]["transport"]["value"] == "교통편 확인 필요"


def test_hope_taxi_goes_to_the_township_office_and_message_names_a_pharmacy():
    from app.workflow import confirmation_text
    body = report(dict(FIELDS, village="생곡1리", bus_ok="no", hospital_query="서석 서울의원", companion_needed="no"))
    req = client.get(f"/api/requests/{client.post('/vapi/webhook', json=body).json()['id']}").json()
    taxi = next(a for a in req["applications"] if a["type"] == "hope_taxi")
    assert taxi["contact"] == "033-430-4454" and taxi["agency"].startswith("서석면")   # 면사무소 담당 전화
    assert "약국" in confirmation_text(req)                                           # 서울의원 가까운 약국


def test_dashboard_test_button_body_also_works():
    r = client.post("/vapi/tools", json={"village": "서석면 수하리"}).json()
    assert "수하" in r["result"]


def test_find_date_tool_counts_days_on_the_server():
    from app import pipeline
    from datetime import datetime
    import app.main as main
    body = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "d1", "type": "function", "function": {"name": "find_date", "arguments": {"expr": "다음 주 화요일"}}},
        {"id": "d2", "type": "function", "function": {"name": "find_date", "arguments": {"expr": "내일"}}},
        {"id": "d3", "type": "function", "function": {"name": "find_date", "arguments": {"expr": "아무 때나"}}},
    ]}}
    real = pipeline.now_kst
    pipeline.now_kst = lambda: datetime(2026, 11, 20, 3, 0, tzinfo=real().tzinfo)   # 11월 20일 금요일
    try:
        r = client.post("/vapi/tools", json=body).json()["results"]
    finally:
        pipeline.now_kst = real
    assert r[0]["result"].startswith("11월 24일 화요일(오늘부터 4일 뒤)")
    assert "토요일이라 쉬는 병원이 많아요" in r[1]["result"] and "일주일 전에 예약" in r[1]["result"]
    assert "정하지 못했어요" in r[2]["result"]


def test_saved_transcript_uses_the_ai_text_not_the_echo():
    from app.pipeline import parse_vapi_report
    msg = {"artifact": {"messages": [{"role": "bot", "message": "안녕하세요. 한 통에. 면 대유 AI. 상담원입니다."},
                                     {"role": "user", "message": "허리가 아파."}],
                        "messagesOpenAIFormatted": [{"role": "system", "content": "지시문"},
                                                    {"role": "assistant", "content": "안녕하세요, 한 통이면 돼유우~ AI 상담원입니다."},
                                                    {"role": "user", "content": "허리가 아파."},
                                                    {"role": "assistant", "tool_calls": [{"id": "x"}]},
                                                    {"role": "tool", "content": "버스 안내"}]}}
    turns = parse_vapi_report(msg)[0]
    assert turns == [{"role": "ai", "text": "안녕하세요, '한 통이면 돼유' AI 상담원입니다."}, {"role": "elder", "text": "허리가 아파."}]


def test_outside_resident_is_not_filled_from_the_roster():
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, village="춘천 효자동"))).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["pickup"]["village"] is None
    assert any(c["type"] == "lives_outside" for c in req["checks"])
    assert not any(c["type"] == "village_from_roster" for c in req["checks"])
