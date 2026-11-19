"""카카오맵으로 모시러 갈 곳을 실제 주소로 바꾸기. 실제 카카오 서버는 부르지 않는다."""
import io

import pytest
from fastapi.testclient import TestClient

from app import extract as extract_mod, kakao
from app.data import villages
from app.extract import Extraction
from app.main import app as api
from tests.test_api import FIELDS, report

client = TestClient(api)
SUHA = next(v for v in villages() if v.name == "수하리")
HALL = {"place_name": "수하리마을회관", "address_name": "강원특별자치도 홍천군 서석면 수하리 123",
        "road_address_name": "강원특별자치도 홍천군 서석면 수하길 12", "place_url": "http://place.map.kakao.com/1"}
ELSEWHERE = {**HALL, "address_name": "강원특별자치도 춘천시 남면 수하리 9", "road_address_name": "강원특별자치도 춘천시 남면 수하로 9"}


@pytest.fixture
def calls(monkeypatch):
    sent = []

    def fake_search(params):
        sent.append(params)
        return [ELSEWHERE, HALL] if "마을회관" in params["query"] else []

    monkeypatch.setenv("KAKAO_REST_KEY", "test")
    monkeypatch.setattr(kakao, "_search", fake_search)
    monkeypatch.setattr(kakao, "_CACHE", {})
    monkeypatch.setattr(extract_mod, "extract", lambda turns, today: Extraction(**FIELDS))
    return sent


def test_split_place_keeps_the_where_words_for_the_address():
    assert kakao.split_place("수하리 마을회관 앞", SUHA) == ("마을회관", " 앞")
    assert kakao.split_place("마을회관 옆 파란 지붕 집이에요", SUHA) == ("마을회관", " 옆 파란 지붕 집")
    assert kakao.split_place("경로당 근처예요.", SUHA) == ("경로당", " 근처")
    assert kakao.split_place("동쪽 마을회관", SUHA) == ("동쪽 마을회관", "")


def test_finds_the_place_inside_the_elders_village(calls):
    found = kakao.find_pickup("수하리 마을회관 앞", SUHA)
    assert found == {"name": "수하리마을회관", "address": "강원특별자치도 홍천군 서석면 수하길 12",
                     "url": "https://place.map.kakao.com/1", "alts": [], "tail": " 앞"}  # 춘천 수하리는 버린다
    assert calls[0]["query"] == "홍천 서석면 수하리 마을회관" and calls[0]["radius"] == 5000
    assert calls[0]["x"] == SUHA.lon and calls[0]["y"] == SUHA.lat


def test_home_other_village_and_no_key_are_not_searched(calls, monkeypatch):
    assert kakao.find_pickup("집 앞", SUHA) is None                # 집은 지도에서 찾을 수 없다
    assert kakao.find_pickup("겨룬리 마을회관 앞", SUHA) is None   # 다른 리(결운리)를 수하리 안에서 찾으면 틀린다
    assert calls == []
    monkeypatch.delenv("KAKAO_REST_KEY")
    assert kakao.find_pickup("마을회관 앞", SUHA) is None and calls == []


def test_two_halls_in_one_village_need_a_check(calls, monkeypatch):
    """실제 서버에서 본 경우: 수하리에는 수하1리·수하2리 마을회관이 따로 있다(여기서는 명부와 같은 풍암리로 시험)."""
    pung = next(v for v in villages() if v.name == "풍암리")
    hall = {**HALL, "address_name": "강원특별자치도 홍천군 서석면 풍암리 1"}
    monkeypatch.setattr(kakao, "_search", lambda params: [{**hall, "place_name": "풍암2리마을회관"},
                                                          {**hall, "place_name": "풍암1리마을회관"}])
    rid = client.post("/vapi/webhook", json=report(dict(FIELDS, village="풍암리", address_detail="마을회관 앞"))).json()["id"]
    place = client.get(f"/api/requests/{rid}").json()["facts"]["place"]
    assert place["level"] == "check" and "풍암1리마을회관" in place["why"]
    assert kakao.find_pickup("풍암2리 마을회관", pung)["alts"] == []  # 몇 리인지 말하면 확인할 필요 없다


def test_not_found_tries_nearest_then_gives_up(calls):
    assert kakao.find_pickup("한림대학교 후문 앞", SUHA) is None
    assert [c.get("sort") for c in calls] == [None, "distance"]


def test_call_fills_dashboard_agency_request_and_forms(calls):
    import fitz
    from docx import Document
    body = report(dict(FIELDS, address_detail="수하리 마을회관 앞", wheelchair_needed="yes"))
    rid = client.post("/vapi/webhook", json=body).json()["id"]
    req = client.get(f"/api/requests/{rid}").json()
    want = "강원특별자치도 홍천군 서석면 수하길 12 (수하리마을회관) 앞"
    assert req["facts"]["place"]["value"] == want and req["facts"]["place"]["url"] == "https://place.map.kakao.com/1"
    assert {a["fields"].get("상세 주소") for a in req["applications"]} == {want}
    word = Document(io.BytesIO(client.get(f"/api/requests/{rid}/forms/integrated.docx").content))
    assert want in "\n".join(c.text for row in word.tables[0].rows for c in row.cells)
    pdf = fitz.open(stream=client.get(f"/api/requests/{rid}/forms/integrated.pdf").content, filetype="pdf")
    assert want in pdf[0].get_text().replace("\xa0", " ")  # 채운 글자의 빈칸은 \xa0로 읽힌다
