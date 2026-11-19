"""음성 시험(④) 채점: 팀원이 시험 카드(/call/cards)로 건 통화를 서버에서 가져와 카드 정답과 비교한다.

상대 날짜('내일', '다음 주 수요일')는 실제로 건 날을 기준으로 다시 계산한다. 이 계산은 서버 규칙(resolve_date)과
따로 짠 것이다(같은 코드로 정답을 만들면 채점이 의미가 없어서).

사용: python eval/voice_eval.py [--server https://hantong-production.up.railway.app] [--out eval/results]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.data import voice_cards  # noqa: E402
from eval.text_eval import ITEMS, predict_from_req, score  # noqa: E402

WD = "월화수목금토일"


def expected_date(expr: str, called: date) -> str | None:
    s = re.sub(r"\s+", "", expr)
    if s == "오늘":
        return called.isoformat()
    if s == "내일":
        return (called + timedelta(days=1)).isoformat()
    if s == "모레":
        return (called + timedelta(days=2)).isoformat()
    m = re.fullmatch(r"(다음주|이번주)([월화수목금토일])요일", s)
    if m:
        monday = called - timedelta(days=called.weekday())
        return (monday + timedelta(days=(7 if m.group(1) == "다음주" else 0) + WD.index(m.group(2)))).isoformat()
    return None  # '다음 주 중에', '장날쯤', '아직 안 정했어요' = 날짜 하나로 정해지지 않음


def gold_for(card: dict, called: date) -> dict:
    return {"병원": card["병원_정답"], "날짜": expected_date(card["날짜표현"], called) or "모름",
            "시간": card["도착희망시각"] if ":" in card["도착희망시각"] else "모름",
            "동행": card["동행"], "마을": card["출발마을"] or "모름"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://hantong-production.up.railway.app")
    ap.add_argument("--out", default="eval/results")
    args = ap.parse_args()

    cards = {c["call_id"]: c for c in voice_cards()}
    reqs = httpx.get(args.server.rstrip("/") + "/api/requests", timeout=30).json()
    tested = [r for r in reqs if (r.get("call") or {}).get("card") in cards and r.get("status") != "processing"]
    rows = []
    for r in sorted(tested, key=lambda x: x["created_at"]):
        card = cards[r["call"]["card"]]
        called = date.fromisoformat(r["created_at"][:10])
        g = gold_for(card, called)
        p = predict_from_req(r)
        ok = score(g, p)
        row = {"call_id": card["call_id"], "card_id": card["card_id"], "말하는_방식": card["말하는_방식"],
               "request_id": r["id"], "건_날": called.isoformat(), "끝난_이유": r["call"].get("ended_reason")}
        for k in ITEMS:
            row[f"정답_{k}"], row[f"예측_{k}"], row[f"ok_{k}"] = g[k], p[k], ok[k]
        row["JGA"] = all(ok.values())
        rows.append(row)
    if not rows:
        print("아직 시험 카드로 건 통화가 없어요.")
        return
    n = len(rows)
    summary = {"통화수": n, "카드수": len({r['call_id'] for r in rows}),
               "항목별_정확도": {k: round(sum(r[f"ok_{k}"] for r in rows) / n, 3) for k in ITEMS},
               "JGA": round(sum(r["JGA"] for r in rows) / n, 3)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "voice_통화별.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.out, "voice_요약.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in rows:
        bad = [k for k in ITEMS if not r[f"ok_{k}"]]
        print(r["call_id"], r["request_id"], "모두 맞음" if not bad else "틀림: " + ", ".join(
            f"{k}(정답 {r[f'정답_{k}']} / 예측 {r[f'예측_{k}']})" for k in bad))


if __name__ == "__main__":
    main()
