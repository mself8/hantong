from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import extract as extract_mod
from app.extract import Extraction
from app.main import app as api
from app.normalize import resolve_date

client = TestClient(api)


def fake_extract(turns, today):
    elder = " ".join(t["text"] for t in turns if t["role"] == "elder")
    assert "정형외과" in elder
    return Extraction(
        intent="hospital_visit", hospital_query="읍내 정형외과", department="정형외과",
        date_expr="담주 화요일", date=None, time_expr=None,
        companion_needed="yes", companion_reason="무릎이 아파서", village="수하리", mobility=None,
        evidence_hospital="읍내 정형외과 가야 되는데", evidence_date="담주 화요일",
        evidence_companion="혼자는 못 가겠드래요", evidence_village="수하리 살아요",
        summary="다음 주 화요일 홍천읍 정형외과, 동행 필요",
    )


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(extract_mod, "extract", fake_extract)


VAPI_REPORT = {"message": {
    "type": "end-of-call-report",
    "call": {"id": "call-1", "customer": {"number": "+821012345678"}},
    "artifact": {"messages": [
        {"role": "system", "message": "시스템 지시문"},
        {"role": "bot", "message": "안녕하세요. 병원 가는 날을 도와드리는 전화예요."},
        {"role": "user", "message": "담주 화요일에 읍내 정형외과 가야 되는데 혼자는 못 가겠드래요"},
        {"role": "bot", "message": "어느 마을에 사세요?"},
        {"role": "user", "message": "수하리 살아요"},
    ]},
}}


def test_vapi_call_becomes_request():
    r = client.post("/vapi/webhook", json=VAPI_REPORT)
    assert r.status_code == 200
    rid = r.json()["id"]

    req = client.get(f"/api/requests/{rid}").json()
    assert req["status"] == "pending"
    assert req["call"]["caller"] == "010-****-5678"  # 전화번호는 가려서 저장한다
    assert req["resident"]["resident_id"] == "R001"
    assert [t["role"] for t in req["transcript"]] == ["ai", "elder", "ai", "elder"]
    assert req["hospital"]["status"] == "candidates"
    assert req["pickup"]["village"] == "서석면 수하리"
    assert req["pickup"]["point"] is None  # 명부(풍암리)와 다른 마을이라 명부 승차 지점을 쓰지 않는다
    assert any(c["type"] == "village_mismatch" for c in req["checks"])
    today = date.fromisoformat(req["created_at"][:10])
    assert req["visit"]["date"] == resolve_date("담주 화요일", today).isoformat()
    assert {c["type"] for c in req["checks"]} >= {"hospital_unclear", "companion_deadline"}

    req = client.post(f"/api/requests/{rid}/approve", json={"memo": "서울정형외과로 확인"}).json()
    assert req["status"] == "approved" and req["memo"] == "서울정형외과로 확인"
    assert [h["status"] for h in req["history"]] == ["processing", "pending", "approved"]
    assert any(x["id"] == rid for x in client.get("/api/requests?status=approved").json())


def test_text_request_and_failure_is_kept(monkeypatch):
    req = client.post("/api/requests", json={"text": "어르신: 담주 화요일 읍내 정형외과요"}).json()
    assert req["status"] == "pending" and req["intent"] == "hospital_visit"

    def broken(turns, today):
        raise RuntimeError("API 키 없음")
    monkeypatch.setattr(extract_mod, "extract", broken)
    req = client.post("/api/requests", json={"text": "정형외과 가야 돼요"}).json()
    assert req["status"] == "pending" and req["checks"][0]["type"] == "extract_failed"
    assert req["transcript"][0]["text"] == "정형외과 가야 돼요"


def test_other_vapi_events_are_ignored():
    assert client.post("/vapi/webhook", json={"message": {"type": "status-update"}}).json() == {"ok": True}
