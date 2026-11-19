from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import extract as extract_mod
from app.extract import Extraction
from app.main import app as api
from app.normalize import resolve_date

client = TestClient(api)

FIELDS = dict(
    intent="hospital_visit", hospital_query="읍내 정형외과", department="정형외과",
    date_expr="담주 화요일", date=None, time_expr=None,
    companion_needed="yes", companion_reason="무릎이 아파서", wheelchair_needed="no",
    village="수하리", mobility=None,
    evidence_hospital="읍내 정형외과 가야 되는데", evidence_date="담주 화요일",
    evidence_companion="혼자는 못 가겠드래요", evidence_village="수하리 살아요",
    summary="다음 주 화요일 홍천읍 정형외과, 동행 필요",
)


def fake_extract(turns, today):
    elder = " ".join(t["text"] for t in turns if t["role"] == "elder")
    assert "정형외과" in elder
    return Extraction(**FIELDS)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(extract_mod, "extract", fake_extract)


def report(structured=None):
    msg = {
        "type": "end-of-call-report",
        "call": {"id": "call-1", "customer": {"number": "+821012345678"}},
        "artifact": {"messages": [
            {"role": "system", "message": "시스템 지시문"},
            {"role": "bot", "message": "안녕하세요. 병원 가는 날을 도와드리는 전화예요."},
            {"role": "user", "message": "담주 화요일에 읍내 정형외과 가야 되는데 혼자는 못 가겠드래요"},
            {"role": "bot", "message": "어느 마을에 사세요?"},
            {"role": "user", "message": "수하리 살아요"},
        ]},
    }
    if structured is not None:
        msg["artifact"]["structuredOutputs"] = {"so-1": {"name": "hantong_request", "result": structured}}
    return {"message": msg}


def test_vapi_call_to_confirmation_message():
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["status"] == "pending"
    assert req["call"]["caller"] == "010-****-5678"  # 전화번호는 가려서 저장한다
    assert req["resident"]["resident_id"] == "R001"
    assert req["hospital"]["status"] == "candidates"
    assert req["pickup"]["village"] == "서석면 수하리"
    assert req["pickup"]["point"] is None  # 명부(풍암리)와 다른 마을이라 명부 승차 지점을 쓰지 않는다
    assert any(c["type"] == "village_mismatch" for c in req["checks"])
    today = date.fromisoformat(req["created_at"][:10])
    assert req["visit"]["date"] == resolve_date("담주 화요일", today).isoformat()

    # 한 줄 요약은 AI 요약 대신 규칙으로 바로잡은 날짜로 만든다
    day = date.fromisoformat(req["visit"]["date"])
    assert req["summary"].startswith(f"{day.month}월 {day.day}일(") and req["summary"].endswith("읍내 정형외과 · 동행 필요")

    # 수하리는 확인된 희망택시 마을이 아니고 휠체어도 필요 없어서 병원동행 신청서 하나만 만든다
    assert [a["type"] for a in req["applications"]] == ["companion"]
    app = req["applications"][0]
    assert app["status"] == "draft" and app["fields"]["어르신"] == "한○○"

    # 병원을 고르면 아직 제출하지 않은 신청서가 그 병원으로 다시 채워진다
    hosp_id = req["hospital"]["candidates"][0]["id"]
    req = client.post(f"/api/requests/{rid}/hospital", json={"hosp_id": hosp_id}).json()
    assert req["hospital"]["status"] == "matched"
    assert req["applications"][0]["fields"]["병원"] == req["hospital"]["candidates"][0]["name"]
    assert req["hospital"]["candidates"][0]["name"] in req["summary"]

    req = client.post(f"/api/requests/{rid}/submit").json()
    assert req["status"] == "submitted" and req["applications"][0]["status"] == "submitted"
    assert client.post(f"/api/requests/{rid}/submit").status_code == 409  # 두 번 제출할 수 없다

    req = client.post(f"/api/requests/{rid}/applications/A1/respond",
                      json={"result": "confirmed", "detail": "동행 매니저가 9시에 댁으로 찾아갑니다."}).json()
    assert req["status"] == "confirmed"
    assert "동행 매니저가 9시에 댁으로 찾아갑니다." in req["message"]["text"]
    assert req["message"]["text"].startswith("한○○ 어르신,")

    req = client.put(f"/api/requests/{rid}/message", json={"text": "고친 안내문"}).json()
    assert req["message"]["text"] == "고친 안내문"
    req = client.post(f"/api/requests/{rid}/message/send").json()
    assert req["status"] == "notified" and req["message"]["status"] == "sent"
    assert any(x["id"] == rid for x in client.get("/api/requests?status=notified").json())


def test_vapi_structured_output_skips_server_llm(monkeypatch):
    def must_not_run(turns, today):
        raise AssertionError("Vapi가 뽑은 항목이 있으면 서버 LLM을 부르지 않는다")
    monkeypatch.setattr(extract_mod, "extract", must_not_run)
    structured = {**FIELDS, "village": "생곡1리", "wheelchair_needed": "yes", "companion_needed": "no"}
    structured.pop("mobility")  # Vapi는 빈 칸을 빼고 보낼 수 있다
    rid = client.post("/vapi/webhook", json=report(structured)).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["checks"] == [] or req["checks"][0]["type"] != "extract_failed"
    assert req["pickup"]["village"] == "서석면 생곡리"
    # 생곡1리는 확인된 희망택시 마을이고, 휠체어가 필요하다
    assert [a["type"] for a in req["applications"]] == ["hope_taxi", "special_transport"]
    assert "신청서 초안 작성 (Vapi가 뽑은 항목으로)" in [h["note"] for h in req["history"]]


def test_rejected_application_is_told_kindly():
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    client.post(f"/api/requests/{rid}/submit")
    req = client.post(f"/api/requests/{rid}/applications/A1/respond", json={"result": "rejected"}).json()
    assert req["status"] == "confirmed"
    assert "병원동행은 이번에는 어렵습니다." in req["message"]["text"]


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


def test_no_agency_needed_gets_message_draft(monkeypatch):
    """기관 신청이 필요 없으면 바로 안내문 초안을 만들고, 공무원이 보내면 끝난다."""
    monkeypatch.setattr(extract_mod, "extract",
                        lambda turns, today: Extraction(**{**FIELDS, "companion_needed": "no"}))
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["applications"] == [] and req["status"] == "pending"
    assert "말씀하신 내용 잘 받았습니다." in req["message"]["text"]
    req = client.post(f"/api/requests/{rid}/message/send").json()
    assert req["status"] == "notified"


def test_structured_output_as_json_text():
    import json
    rid = client.post("/vapi/webhook", json=report(json.dumps(FIELDS, ensure_ascii=False))).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert "신청서 초안 작성 (Vapi가 뽑은 항목으로)" in [h["note"] for h in req["history"]]


def test_web_call_without_number_uses_demo_resident(monkeypatch):
    monkeypatch.setenv("DEMO_CALLERS", "web:R006,01012345678:R001")
    body = report()
    body["message"]["call"] = {"id": "web-1", "type": "webCall"}
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["resident"]["resident_id"] == "R006" and req["call"]["caller"] is None
    assert req["pickup"]["point"] == "수하리 마을회관"  # 명부와 같은 마을이라 승차 지점을 채운다


def test_messy_vapi_values_do_not_break_the_request():
    from app.extract import from_dict
    ex = from_dict({**FIELDS, "companion_needed": "Yes", "wheelchair_needed": "아니요", "intent": "병원",
                    "date": "11월 24일", "time_expr": "", "evidence_village": "없음", "mobility": "모름"})
    assert ex.companion_needed == "yes" and ex.wheelchair_needed == "unknown" and ex.intent == "other"
    assert ex.date is None and ex.time_expr is None and ex.evidence_village is None and ex.mobility is None
    assert from_dict({**FIELDS, "date": "2026-02-30"}).date is None
