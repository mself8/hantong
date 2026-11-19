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
    village="수하리", address_detail=None, mobility=None,
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

    # 수하리는 확인된 희망택시 마을이 아니고 휠체어도 필요 없어서 병원동행 요청 하나만 만든다
    assert [a["type"] for a in req["applications"]] == ["companion"]
    app = req["applications"][0]
    assert app["status"] == "draft" and app["fields"]["어르신"] == "한옥순"

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
    assert "요금: 첫 1시간 5,000원" in req["message"]["text"]  # 확정 안내문에 요금이 붙는다
    assert req["message"]["text"].startswith("한옥순 어르신,")

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
    taxi, special = req["applications"]
    assert taxi["fields"]["요금"].startswith("1,700원") and any("이장" in n for n in taxi["notes"])
    assert special["fields"]["교통약자 등록"] == "미등록"  # 명부(R001)상 특별교통수단 미등록
    assert any("등록이 안 된 분" in n for n in special["notes"])
    assert any("교통복지카드가 있어" in n for n in taxi["notes"])  # R001은 카드 있음
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


def test_call_page_injects_config_from_env(monkeypatch):
    html = client.get("/call").text
    assert "window.HANTONG" in html and '"publicKey": ""' in html  # 키가 없으면 '설정 중'으로 보인다
    monkeypatch.setenv("VAPI_PUBLIC_KEY", "pk-test")
    html = client.get("/call").text
    assert '"publicKey": "pk-test"' in html and "e441e44c" in html


def test_address_detail_becomes_pickup_point(monkeypatch):
    monkeypatch.setattr(extract_mod, "extract", lambda turns, today: Extraction(
        **{**FIELDS, "address_detail": "수하리 마을회관 옆 파란 지붕 집"}))
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["pickup"]["detail"] == "수하리 마을회관 옆 파란 지붕 집"
    assert req["pickup"]["point"] == "수하리 마을회관 옆 파란 지붕 집"
    assert req["applications"][0]["fields"]["상세 주소"] == "수하리 마을회관 옆 파란 지붕 집"


def test_low_income_resident_gets_reduced_companion_fee(monkeypatch):
    monkeypatch.setenv("DEMO_CALLERS", "web:R002")  # R002는 명부상 기초수급·차상위
    body = report()
    body["message"]["call"] = {"id": "web-2", "type": "webCall"}
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    app = client.get(f"/api/requests/{rid}").json()["applications"][0]
    assert app["fields"]["요금"].startswith("첫 1시간 1,000원")


def test_view_page_shows_forms_and_message():
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    client.post(f"/api/requests/{rid}/submit")
    client.post(f"/api/requests/{rid}/applications/A1/respond",
                json={"result": "confirmed", "detail": "동행 매니저가 9시에 찾아갑니다.<b>"})
    html = client.get("/view").text
    assert "병원동행 예약" in html and "어르신께 보낼 안내문" in html
    assert "동행 매니저가 9시에 찾아갑니다.&lt;b&gt;" in html  # 값은 이스케이프해서 보여 준다
    assert client.get("/").status_code == 200


def test_voice_card_call_is_tagged_and_not_linked_to_roster():
    body = report()
    body["message"]["call"] = {"id": "web-v01", "type": "webCall", "assistantOverrides": {"metadata": {"card": "V01"}}}
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["call"]["card"] == "V01" and req["resident"] is None
    assert len(client.get("/api/voice-cards").json()) == 18
    assert "V18" in client.get("/call/cards").text


def test_unheard_village_and_changed_date_are_flagged(monkeypatch):
    """통화 시험에서 나온 경우: 모르는 마을을 명부로 조용히 채우지 않고, 바뀐 날짜는 경고한다."""
    monkeypatch.setattr(extract_mod, "extract", lambda turns, today: Extraction(**{
        **FIELDS, "village": "교동리", "hospital_query": None, "department": None, "evidence_hospital": "배가 아파요",
        "date_expr": "다음 주 화요일", "date": "2030-01-01"}))
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    types = {c["type"]: c["level"] for c in req["checks"]}
    assert types.get("village_from_roster") == "warn" and types.get("date_mismatch") == "warn"
    assert req["hospital"]["department"] == "내과" and req["hospital"]["basis"] == "department"


def test_official_forms_download_one_at_a_time():
    import fitz
    body = report(dict(FIELDS, wheelchair_needed="yes"))
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    assert client.get(f"/api/requests/{rid}").json()["needs_registration"]  # 미등록이라 등록 신청서가 필요
    pages = {}
    for kind in ("integrated", "special_transport"):
        r = client.get(f"/api/requests/{rid}/forms/{kind}.pdf")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        doc = fitz.open(stream=r.content, filetype="pdf")
        pages[kind] = (doc.page_count, "".join(p.get_text() for p in doc))
    assert pages["integrated"][0] == 2 and "통합지원" in pages["integrated"][1] and "한옥순" in pages["integrated"][1]
    assert pages["special_transport"][0] == 4 and "등록 신청서" in pages["special_transport"][1]
    assert client.get(f"/api/requests/{rid}/forms/companion.pdf").status_code == 404  # 우리가 지어낸 양식은 없다


def test_spoken_name_is_used_and_checked_against_the_roster():
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, caller_name="김순자"))).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["display_name"] == "김순자" and req["applications"][0]["fields"]["어르신"] == "김순자"
    assert any(c["type"] == "name_mismatch" for c in req["checks"])  # 이 번호로 등록된 이름(한옥순)과 다르다


def test_town_only_is_kept_instead_of_roster_village():
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, village="홍천읍", address_detail="터미널 근처"))).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["pickup"]["village"] == "홍천읍"                         # 등록된 주소(서석면 풍암리)로 바꾸지 않는다
    assert any(c["type"] == "village_town_only" for c in req["checks"])
    assert req["facts"]["place"]["why"] == "읍·면까지만 들었어요" and req["facts"]["transport"]["level"] == "check"


def test_stored_ai_turns_use_our_service_name():
    body = report()
    body["message"]["artifact"]["messages"][1] = {"role": "bot", "message": "안녕하세요, 홍천군 한통의면 대유AI 상담원입니다."}
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    ai = [t["text"] for t in client.get(f"/api/requests/{rid}").json()["transcript"] if t["role"] == "ai"]
    assert "안녕하세요, 홍천군 '한 통이면 돼유' AI 상담원입니다." in ai


def test_agency_other_reply_is_kept_as_memo():
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    client.post(f"/api/requests/{rid}/submit")
    path = f"/api/requests/{rid}/applications/A1/respond"
    req = client.post(path, json={"result": "note", "detail": "날짜를 하루 미뤄 달래요"}).json()
    app_ = req["applications"][0]
    assert app_["status"] == "submitted" and app_["replies"][0]["note"] == "날짜를 하루 미뤄 달래요"  # 기타는 상태를 바꾸지 않는다
    assert client.post(path, json={"result": "note", "detail": " "}).status_code == 409
    assert client.post(path, json={"result": "confirmed"}).json()["applications"][0]["status"] == "confirmed"


def test_tiles_always_have_a_value_and_say_how_sure():
    rid = client.post("/vapi/webhook", json=report()).json()["id"]
    f = client.get(f"/api/requests/{rid}").json()["facts"]
    assert f["date"]["level"] == "ok" and f["date"]["sub"] == "담주 화요일"        # 말한 표현을 규칙으로 계산한 날짜
    assert f["time"] == {"value": "모름", "sub": "", "level": "missing", "why": "통화에서 진료 시각이 나오지 않았어요"}
    assert f["hospital"]["level"] == "check" and f["hospital"]["value"]             # 후보가 있으면 첫 후보를 보여 준다
    assert f["place"]["level"] == "check" and f["place"]["why"] == "등록된 주소와 달라요"
    assert f["transport"]["level"] == "check"                                       # 마을이 엇갈리면 교통수단도 확인 필요
    assert f["companion"] == {"value": "병원동행", "sub": "같이 가 드릴 분 필요", "level": "ok", "why": None}
    hid = client.get(f"/api/requests/{rid}").json()["hospital"]["candidates"][0]["id"]
    f = client.post(f"/api/requests/{rid}/hospital", json={"hosp_id": hid}).json()["facts"]
    assert f["hospital"]["level"] == "ok"                                           # 공무원이 고르면 확인 끝


def test_wheelchair_means_special_transport():
    structured = {**FIELDS, "wheelchair_needed": "yes", "village": "풍암리", "address_detail": "마을회관 앞", "time_expr": "오전 10시"}
    rid = client.post("/vapi/webhook", json=report(structured)).json()["id"]
    f = client.get(f"/api/requests/{rid}").json()["facts"]
    assert f["transport"]["value"] == "특별교통수단" and f["time"]["value"] == "10:00"
    assert f["place"] == {"value": "서석면 풍암리", "sub": "마을회관 앞", "level": "ok", "why": None}


def test_official_forms_as_word():
    from docx import Document
    import io
    body = report(dict(FIELDS, wheelchair_needed="yes", caller_name="한옥순"))
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    for kind in ("integrated", "special_transport"):
        r = client.get(f"/api/requests/{rid}/forms/{kind}.docx")
        assert r.status_code == 200 and "wordprocessingml" in r.headers["content-type"]
        doc = Document(io.BytesIO(r.content))
        text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        assert "한옥순" in text and rid in text
    assert "[√] 대상자 본인" in "\n".join(c.text for row in Document(io.BytesIO(
        client.get(f"/api/requests/{rid}/forms/integrated.docx").content)).tables[0].rows for c in row.cells)
