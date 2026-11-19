"""통화 글에서 신청서 항목을 뽑는다 (LLM).

LLM은 어르신이 한 말에서 항목을 옮겨 적기만 한다. 병원 이름 맞추기, 날짜 계산 검산, 진료일 확인은
normalize.py와 validate.py의 규칙이 맡는다.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field

from .data import WEEKDAYS

MODEL = os.getenv("EXTRACT_MODEL", "claude-opus-5-5")


class Extraction(BaseModel):
    intent: Literal["hospital_visit", "medication", "cancel", "companion", "other"] = Field(
        description="hospital_visit=병원 가는 날 신청, medication=약 먹는 법 문의, cancel=신청 취소, "
                    "companion=동행만 문의, other=그 밖의 말")
    hospital_query: Optional[str] = Field(description="어르신이 말한 병원 표현 그대로. 예: '읍내 정형외과', '아산병원'")
    department: Optional[str] = Field(description="진료과. 어르신이 말했거나 아픈 곳으로 분명할 때만. 예: 눈→안과")
    date_expr: Optional[str] = Field(description="어르신이 말한 날짜 표현 그대로. 예: '담주 화요일'")
    date: Optional[str] = Field(description="통화일 기준으로 계산한 진료 날짜 YYYY-MM-DD. 범위이거나 애매하면 null")
    time_expr: Optional[str] = Field(description="어르신이 말한 시간 표현 그대로. 예: '열 시 반', '오전'")
    companion_needed: Literal["yes", "no", "unknown"] = Field(
        description="같이 가 줄 사람이 필요하다고 했으면 yes, 혼자 갈 수 있다고 했으면 no, 말이 없으면 unknown")
    companion_reason: Optional[str] = Field(description="동행이 필요한 이유를 어르신 말로. 예: '무릎이 아파서'")
    wheelchair_needed: Literal["yes", "no", "unknown"] = Field(
        description="휠체어를 타야 하거나 휠체어 차량이 필요하다고 했으면 yes, 아니라고 했으면 no, 말이 없으면 unknown")
    village: Optional[str] = Field(description="어르신이 사는 마을(리) 표현 그대로. 예: '수하리'")
    mobility: Optional[str] = Field(description="거동 상태를 말했으면 그대로. 예: '지팡이 짚어요'")
    evidence_hospital: Optional[str] = Field(description="병원 값의 근거가 된 어르신 말 원문 조각")
    evidence_date: Optional[str] = Field(description="날짜 값의 근거가 된 어르신 말 원문 조각")
    evidence_companion: Optional[str] = Field(description="동행 값의 근거가 된 어르신 말 원문 조각")
    evidence_village: Optional[str] = Field(description="마을 값의 근거가 된 어르신 말 원문 조각")
    summary: str = Field(description="공무원이 볼 한 줄 요약. 예: '11/24(화) 홍천읍 정형외과, 동행 필요(무릎 통증)'")


SYSTEM = """너는 강원 홍천군 어르신의 '병원 가는 날' 신청 전화를 신청서로 옮기는 도우미다.

규칙:
- 어르신(어르신:)이 실제로 말한 내용만 옮긴다. 말하지 않은 값은 추측하지 말고 null이나 unknown으로 둔다.
- AI(AI:)가 확인 질문으로 말한 내용은 어르신이 "네", "그려" 처럼 맞다고 답했을 때만 값으로 쓴다.
- 강원 사투리를 표준어 뜻으로 이해한다. 예: '담주'=다음 주, '가우'=가요, '못 가겠드래요'=못 가겠어요.
- 병원 이름은 지어내지 않는다. 어르신이 말한 표현을 hospital_query에 그대로 적는다.
- 날짜는 아래 통화일 기준으로 계산한다. '다음 주 화요일'은 통화일이 속한 주의 다음 주 화요일이다.
- evidence_* 에는 그 값을 뽑은 근거가 된 어르신 말을 통화 기록에서 그대로 짧게 옮긴다.
- 진단, 처방, 지원 자격 판단은 하지 않는다."""


EMPTY = {"", "없음", "모름", "null", "none", "n/a", "-"}


def from_dict(d: dict) -> Extraction:
    """Vapi 구조화 출력을 Extraction으로 맞춘다.

    Vapi 화면에서는 선택지를 강제할 수 없어서, 빈칸·'없음'은 비우고 선택지 밖의 값과
    날짜 형식이 아닌 날짜는 기본값으로 둔다. 그래야 값 하나 때문에 신청서 전체가 실패하지 않는다.
    """
    base = {k: None for k in Extraction.model_fields}
    base.update(intent="other", companion_needed="unknown", wheelchair_needed="unknown", summary="")
    for k, v in (d or {}).items():
        if k not in Extraction.model_fields or v is None:
            continue
        v = str(v).strip()
        choices = get_args(Extraction.model_fields[k].annotation)
        if choices and all(isinstance(c, str) for c in choices):  # 선택지 칸
            v = v.lower()
            if v in choices:
                base[k] = v
        elif k == "date":
            base[k] = v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and _valid_date(v) else None
        elif v.lower() not in EMPTY:
            base[k] = v
    return Extraction.model_validate(base)


def _valid_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def format_transcript(turns: list[dict]) -> str:
    who = {"elder": "어르신", "ai": "AI"}
    return "\n".join(f"{who.get(t['role'], t['role'])}: {t['text']}" for t in turns if t.get("text"))


def extract(turns: list[dict], today: date) -> Extraction:
    """Claude로 신청서 항목을 뽑는다. ANTHROPIC_API_KEY가 필요하다."""
    import anthropic

    client = anthropic.Anthropic()
    user = (f"통화일: {today.isoformat()} ({WEEKDAYS[today.weekday()]}요일)\n\n"
            f"통화 기록:\n{format_transcript(turns)}")
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=Extraction,
    )
    if response.parsed_output is None:
        raise RuntimeError(f"추출 결과가 비었어요 (stop_reason={response.stop_reason})")
    return response.parsed_output
