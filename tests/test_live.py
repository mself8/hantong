from fastapi.testclient import TestClient

from app import live
from app.main import app as api

client = TestClient(api)


def say(cid, role, kind, text):
    return {"type": "transcript", "call": {"id": cid, "type": "inboundPhoneCall"},
            "role": role, "transcriptType": kind, "transcript": text}


def convo(cid, *ai_texts):
    msgs = [{"role": "system", "content": "지시문"}] + [{"role": "assistant", "content": t} for t in ai_texts]
    return {"type": "conversation-update", "call": {"id": cid}, "messagesOpenAIFormatted": msgs}


def turns(cid):
    return [(t["role"], t["text"], t["partial"]) for t in live.snapshot(cid)["turns"]]


def test_partial_then_final_and_exact_ai_text():
    c = "live-1"
    live.update(say(c, "assistant", "partial", "안녕하세요. 홍천군"))
    assert turns(c) == [("ai", "", "안녕하세요. 홍천군")]
    live.update(say(c, "assistant", "final", "안녕하세요. 홍천군 한 통이면 대유 AI 상담원입니다."))
    # AI 원문이 오면 받아 적은 오타(대유)를 원문(돼유)으로 바꾼다
    live.update(convo(c, "안녕하세요, 홍천군 '한 통이면 돼유' AI 상담원입니다."))
    live.update(say(c, "user", "partial", "병원을 좀"))
    live.update(say(c, "user", "final", "병원을 좀 가야 하는데."))
    live.update(say(c, "user", "partial", "병원을 좀 가야"))  # 늦게 도착한 부분 글자는 무시
    live.update(say(c, "user", "final", "읍내 내과요."))      # 같은 차례면 한 말풍선에 이어 쓴다
    assert turns(c) == [("ai", "안녕하세요, 홍천군 '한 통이면 돼유' AI 상담원입니다.", ""),
                        ("elder", "병원을 좀 가야 하는데. 읍내 내과요.", "")]
    snap = live.snapshot(c)
    assert snap["kind"] == "phone" and not snap["ended"]


def test_exact_text_before_speech_is_not_doubled():
    c = "live-2"
    live.update(say(c, "user", "final", "네."))
    live.update(convo(c, "어느 병원에 가세요?"))                # 말하기 전에 원문이 먼저 온 경우
    live.update(say(c, "assistant", "final", "어느 병원에 가세요?"))  # 같은 말을 받아 적은 글자는 버린다
    assert turns(c) == [("elder", "네.", ""), ("ai", "어느 병원에 가세요?", "")]


def test_report_ends_call_and_links_request():
    c = "live-3"
    live.update(say(c, "assistant", "final", "어떤 일로 전화 주셨어요?"))
    live.update(say(c, "user", "partial", "병원"))
    live.finish({"call": {"id": c}}, "Q0000-TEST", [])
    snap = live.snapshot(c)
    assert snap["ended"] and snap["request_id"] == "Q0000-TEST"
    assert turns(c)[-1] == ("elder", "병원", "")  # 끝날 때 남은 부분 글자는 그대로 남긴다
    live.update(say(c, "user", "final", "뒤늦게 온 글자"))  # 끝난 통화에는 더 쓰지 않는다
    assert turns(c)[-1] == ("elder", "병원", "")


def test_webhook_feeds_live_view_and_report_fills_missing_turns():
    msg = say("live-4", "user", "final", "다음 주 화요일이요.")
    assert client.post("/vapi/webhook", json={"message": msg}).json() == {"ok": True}
    assert client.get("/api/live/live-4").json()["turns"][0]["text"] == "다음 주 화요일이요."
    assert client.get("/api/live").json()["id"] == "live-4"  # 가장 최근 통화
    assert client.get("/api/live/none").json() == {"id": None}
    # 통화 중 메시지가 없었던 통화는 끝 보고서의 대화로 채운다(어르신 말이 없으면 신청서는 안 만든다)
    report = {"type": "end-of-call-report", "call": {"id": "live-5"},
              "artifact": {"messages": [{"role": "bot", "message": "안녕하세요."}]}}
    client.post("/vapi/webhook", json={"message": report})
    snap = client.get("/api/live/live-5").json()
    assert snap["ended"] and snap["skipped"] and snap["turns"] == [{"role": "ai", "text": "안녕하세요.", "partial": ""}]
