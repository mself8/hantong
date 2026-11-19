"""통화 중 주소 확인 도구. 11-20 시험 통화에서 본 실패를 그대로 시험한다."""
from fastapi.testclient import TestClient

from app import address, main
from app.main import app as api
from tests.test_api import FIELDS, report

client = TestClient(api)


def test_village_number_and_lot():
    r = address.check("서석면 청량2리 30번지")
    assert r["status"] == "ok" and r["label"] == "서석면 청량2리 30번지" and r["detail"] == "청량2리 30번지"
    assert address.check("서석면 청량이리 30번지")["detail"] == "청량2리 30번지"  # 숫자를 소리 나는 대로 받아 적어도
    r = address.check("청량 1리")
    assert r["label"] == "서석면 청량1리" and r["detail"] is None and "번지를 여쭤" in r["message"]


def test_other_town_unknown_and_outside():
    r = address.check("홍천읍 능평리")  # 능평리는 북방면
    assert r["status"] == "other_town" and "북방면" in r["message"]
    r = address.check("홍천읍 상수리")  # 없는 리
    assert r["status"] == "unknown" and "'상수리'는 홍천군 마을 목록에 없어요" in r["message"] and "영귀미면 성수리" in r["message"]
    assert address.check("홍천읍 겨룬리")["status"] == "unknown"  # 소리로 맞춘 리는 어르신께 확인받는다
    assert address.check("홍천읍 결운리 50번지")["label"] == "홍천읍 결운리 50번지"
    assert address.check("춘천 효자동")["status"] == "outside"
    assert address.check("서석면")["status"] == "town_only"


def test_last_checked_address_goes_on_the_request():
    """AI가 '청량1리'로 베껴 말해도, 마지막에 확인한 '청량2리'가 신청서에 들어간다."""
    def ask(said):
        body = {"message": {"type": "tool-calls", "call": {"id": "call-1"}, "toolCallList": [
            {"id": "t1", "function": {"name": "check_address", "arguments": {"said": said}}}]}}
        return client.post("/vapi/tools", json=body).json()["results"][0]["result"]

    assert "서석면 청량1리 30번지" in ask("서석면 청량1리 30번지")
    assert "서석면 청량2리 30번지" in ask("청량2리 30번지")
    body = report(dict(FIELDS, village="청량1리", address_detail="청량1리 30번지"))
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    assert req["pickup"]["village"] == "서석면 청량리" and req["pickup"]["detail"] == "청량2리 30번지"
    assert "call-1" not in main.ADDRESS  # 통화가 끝나면 지운다


def test_dashboard_test_button_sends_raw_args():
    assert "북방면" in client.post("/vapi/tools", json={"said": "홍천읍 능평리"}).json()["result"]
