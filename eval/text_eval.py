"""가상 통화 평가 자료(다연 v1)로 접수 파이프라인을 채점한다.

모드
- rules: 정답 근거 문장을 그대로 넣어 서버 규칙(날짜·시간·병원·마을 맞추기)만 점검한다. LLM 없음.
- llm:   대화 전체를 로컬 LLM(OpenAI 호환 주소)이 항목으로 뽑고, 서버 규칙을 거쳐 신청서를 만든다.
         뽑는 지시와 칸 설명은 Vapi 구조화 출력(hantong_request)과 같다. 시연 모델(GPT)과는 다른 모델이다.

채점은 자료 안내(00_사용안내.md)를 따른다. 항목별 정확도, 다섯 항목이 모두 맞은 비율(JGA),
카드마다 세 대화가 모두 맞은 비율(pass^3)을 낸다.

사용: python eval/text_eval.py --mode rules --data <데이터세트 폴더> --out eval/results
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import os
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.extract import Extraction, from_dict  # noqa: E402
from app.pipeline import fill  # noqa: E402

ITEMS = ["병원", "날짜", "시간", "동행", "마을"]
NO_VALUE = {"확인 필요", "모름", "", None}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def load(data_dir: str, split: str = "02_평가"):
    sub = {nfc(os.path.basename(d.rstrip("/"))): d for d in glob.glob(os.path.join(data_dir, "*/"))}
    files = {nfc(os.path.basename(f)): f for f in glob.glob(os.path.join(sub[split], "*"))}
    dialogs = [json.loads(line) for line in open(files["참조대화.jsonl"], encoding="utf-8")]
    gold = {g["dialog_id"]: g for g in (json.loads(line) for line in open(files["근거정답.jsonl"], encoding="utf-8"))}
    cards = {r["card_id"]: r for r in csv.DictReader(open(files["정답카드.csv"], encoding="utf-8-sig"))}
    return dialogs, gold, cards


def predict_from_req(req: dict) -> dict:
    """서버가 만든 신청서에서 채점할 다섯 항목을 꺼낸다."""
    hosp = req.get("hospital") or {}
    cands = hosp.get("candidates") or []
    if hosp.get("basis") == "department":  # 이름 없이 진료과로만 고른 후보 = 병원 이름 모름
        h = "모름"
    elif hosp.get("status") == "matched" and cands:
        h = cands[0]["name"]
    elif hosp.get("status") == "candidates" and cands:
        h = "확인 필요"
    else:
        h = "모름"
    visit = req.get("visit") or {}
    return {"병원": h, "병원_후보": [c["name"] for c in cands] if h == "확인 필요" else [],
            "날짜": visit.get("date"), "시간": visit.get("time"),
            "동행": (req.get("companion") or {}).get("needed"),
            "마을": (req.get("pickup") or {}).get("village")}


def score(gold: dict, pred: dict) -> dict:
    """항목마다 맞았는지. 정답이 '확인 필요'·'모름'이면 서버가 값을 지어내지 않아야 맞다."""
    ok = {}
    g = gold
    if g["병원"] == "확인 필요":
        ok["병원"] = pred["병원"] == "확인 필요"
    elif g["병원"] in NO_VALUE:
        ok["병원"] = pred["병원"] == "모름"
    else:
        ok["병원"] = pred["병원"] == g["병원"]
    for k in ("날짜", "시간", "마을"):
        ok[k] = (pred[k] in NO_VALUE) if g[k] in NO_VALUE else (pred[k] == g[k])
    ok["동행"] = pred["동행"] == g["동행"]
    return ok


def run_rules(dialog: dict, gold: dict, card: dict) -> dict:
    """정답 근거 문장을 LLM이 완벽히 뽑았다고 보고 규칙만 돌린다. 동행은 LLM 몫이라 정답을 그대로 넣는다."""
    ev = gold["근거"]
    q = {k: (ev.get(k) or {}).get("quote") for k in ITEMS}
    ex = from_dict({"intent": "hospital_visit", "hospital_query": q["병원"], "department": card.get("진료과"),
                    "date_expr": q["날짜"], "time_expr": q["시간"], "village": q["마을"],
                    "companion_needed": gold["설계_정답"]["동행"], "wheelchair_needed": "unknown", "summary": ""})
    return build(ex, dialog["통화일"])


def build(ex: Extraction, call_date: str) -> dict:
    req = {"id": "eval", "created_at": call_date + "T09:00:00+09:00", "resident": None, "call": {},
           "transcript": [], "checks": [], "history": []}
    return fill(req, ex, date.fromisoformat(call_date))


# ── LLM 추출 (Vapi 구조화 출력과 같은 지시) ─────────────────

FIELD_DESC = {
    "intent": "통화 목적. 다음 중 하나만 쓴다: hospital_visit(병원 가는 날 신청), medication(약 문의), cancel(신청 취소), companion(동행만 문의), other(그 밖).",
    "hospital_query": "어르신이 가겠다고 한 병원 표현 그대로. 상담원이 말씀드린 병원 중에서 고르셨으면 그 병원 이름을 쓴다(예: '서울삼성내과의원'). 말하지 않았으면 비운다. 병원 이름을 지어내지 않는다.",
    "department": "진료과. 병원 이름이 없어도 아픈 곳으로 분명하면 쓴다(배·속·혈압은 내과, 무릎·허리는 정형외과, 눈은 안과, 이는 치과, 귀·코·목은 이비인후과). 모르면 비운다.",
    "date_expr": "어르신이 말한 날짜 표현 그대로. 말을 바꾸면 가장 나중에 말한 표현을 쓴다(예: '다음 주'라고 했다가 '다다음 주'로 고치면 '다다음 주 화요일'). 말하지 않았으면 비운다.",
    "date": "통화에서 확인한 진료 날짜를 YYYY-MM-DD로 쓴다. 상담원이 '○월 ○일 맞으세요?'라고 확인한 날짜를 따른다. 연도는 2026년, 1~10월이면 2027년. 모르면 비운다.",
    "time_expr": "어르신이 말한 시간 표현 그대로. 예: '열 시 반', '아침 일찍'. 말하지 않았으면 비운다.",
    "companion_needed": "병원에 같이 가 줄 사람이 필요한지. yes(필요), no(혼자 갈 수 있음), unknown(말 없음) 중 하나만 쓴다.",
    "companion_reason": "같이 가 줄 사람이 필요한 이유를 어르신 말로. 예: 무릎이 아파서. 없으면 비운다.",
    "wheelchair_needed": "휠체어를 타야 하거나 휠체어 차량이 필요하면 yes, 아니라고 했으면 no, 말이 없으면 unknown. 셋 중 하나만 쓴다.",
    "village": "어르신이 사는 마을(리) 표현 그대로. 예: '수하리'. 말하지 않았으면 비운다.",
    "address_detail": "번지·집 위치·가까운 건물 등 사는 곳의 자세한 설명을 어르신 말 그대로. 예: '수하리 123번지', '마을회관 옆 파란 지붕 집'. 말하지 않았으면 비운다.",
    "mobility": "거동 상태를 어르신 말로. 예: 지팡이 짚음, 보행기. 말하지 않았으면 비운다.",
    "evidence_hospital": "병원 항목의 근거가 된 어르신 말 한 구절을 고치지 말고 그대로 옮긴다. 없으면 비운다.",
    "evidence_date": "날짜 항목의 근거가 된 어르신 말 한 구절을 고치지 말고 그대로 옮긴다. 없으면 비운다.",
    "evidence_companion": "동행 항목의 근거가 된 어르신 말 한 구절을 고치지 말고 그대로 옮긴다. 없으면 비운다.",
    "evidence_village": "마을 항목의 근거가 된 어르신 말 한 구절을 고치지 말고 그대로 옮긴다. 없으면 비운다.",
    "summary": "신청 내용 한 줄 요약. 예: 11/24(화) 홍천읍 정형외과, 동행 필요.",
}
INSTRUCTION = ("어르신 전화 통화에서 '병원 가는 날' 신청서 항목을 뽑는다. 어르신이 실제로 말한 것만 옮기고, "
               "말하지 않은 칸은 비운다. 병원 이름은 지어내지 않는다. 말이 바뀌면 가장 나중에 말한 것을 따른다.\n\n"
               "칸 설명:\n" + "\n".join(f"- {k}: {v}" for k, v in FIELD_DESC.items()))
SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in FIELD_DESC},
          "required": list(FIELD_DESC), "additionalProperties": False}


def transcript_text(dialog: dict) -> str:
    return "\n".join(f"{'어르신' if t['role'] == 'elder' else 'AI'}: {t['text']}" for t in dialog["turns"])


async def llm_extract(client, url: str, model: str, dialog: dict) -> dict:
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": INSTRUCTION},
                         {"role": "user", "content": "통화 기록:\n" + transcript_text(dialog)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "hantong_request", "schema": SCHEMA}},
            "chat_template_kwargs": {"enable_thinking": False}}
    r = await client.post(url.rstrip("/") + "/chat/completions", json=body, timeout=180)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


async def run_llm_all(dialogs, url, model, concurrency):
    import httpx
    sem = asyncio.Semaphore(concurrency)
    out = {}
    async with httpx.AsyncClient() as client:
        async def one(d):
            async with sem:
                t0 = time.time()
                try:
                    out[d["dialog_id"]] = (await llm_extract(client, url, model, d), time.time() - t0, None)
                except Exception as e:  # 실패도 기록한다
                    out[d["dialog_id"]] = (None, time.time() - t0, f"{type(e).__name__}: {e}")
        await asyncio.gather(*(one(d) for d in dialogs))
    return out


# ── 집계 ─────────────────────────────────────────────

def summarize(rows: list[dict], items: list[str]) -> dict:
    n = len(rows)
    acc = {k: round(sum(r[f"ok_{k}"] for r in rows) / n, 3) for k in items}
    jga = round(sum(all(r[f"ok_{k}"] for k in items) for r in rows) / n, 3)
    by_card = defaultdict(list)
    for r in rows:
        by_card[r["card_id"]].append(all(r[f"ok_{k}"] for k in items))
    pass3 = round(sum(all(v) for v in by_card.values()) / len(by_card), 3)
    by_style = defaultdict(list)
    for r in rows:
        by_style[r["말하는_방식"]].append(all(r[f"ok_{k}"] for k in items))
    return {"대화수": n, "카드수": len(by_card), "항목별_정확도": acc, "JGA": jga, "pass^3": pass3,
            "말하는_방식별_JGA": {s: round(sum(v) / len(v), 3) for s, v in sorted(by_style.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--llm-url", default="http://127.0.0.1:8001/v1")
    ap.add_argument("--model", default="qwen3.8-27b")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--split", default="02_평가", help="02_평가(E 60장) 또는 01_개발(D 10장)")
    args = ap.parse_args()

    dialogs, gold, cards = load(args.data, args.split)
    os.makedirs(args.out, exist_ok=True)
    llm = asyncio.run(run_llm_all(dialogs, args.llm_url, args.model, args.concurrency)) if args.mode == "llm" else {}

    rows, fails = [], []
    for d in dialogs:
        g = gold[d["dialog_id"]]
        gd = g["설계_정답"]
        row = {"dialog_id": d["dialog_id"], "card_id": d["card_id"], "말하는_방식": g["말하는_방식"]}
        if args.mode == "rules":
            req = run_rules(d, g, cards[d["card_id"]])
        else:
            raw, secs, err = llm[d["dialog_id"]]
            row["초"] = round(secs, 1)
            if raw is None:
                row["오류"] = err
                req = build(from_dict({}), d["통화일"])
            else:
                req = build(from_dict(raw), d["통화일"])
                row["추출"] = json.dumps({k: raw.get(k) for k in ("hospital_query", "date_expr", "time_expr",
                                                                   "companion_needed", "village")}, ensure_ascii=False)
        pred = predict_from_req(req)
        ok = score(gd, pred)
        for k in ITEMS:
            row[f"정답_{k}"] = gd[k]
            row[f"예측_{k}"] = pred[k]
            row[f"ok_{k}"] = ok[k]
        rows.append(row)
        if not all(ok[k] for k in (ITEMS if args.mode == "llm" else ["병원", "날짜", "시간", "마을"])):
            fails.append(row)

    items = ITEMS if args.mode == "llm" else ["병원", "날짜", "시간", "마을"]
    summary = summarize(rows, items)
    summary.update({"모드": args.mode, "모델": args.model if args.mode == "llm" else None,
                    "실패_대화수": len(fails),
                    "LLM_오류수": sum(1 for r in rows if r.get("오류"))})
    stem = f"{args.mode}" + ("_개발" if args.split.startswith("01") else "")
    with open(os.path.join(args.out, f"{stem}_대화별.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + [k for k in ("초", "오류", "추출") if k not in rows[0]])
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.out, f"{stem}_요약.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
