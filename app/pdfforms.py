"""신청 한 건을 기관에 낼 PDF 묶음으로 만든다.

1. 통합지원 신청서 (공식 양식 위에 값 채움)
2. 특별교통수단 미등록(또는 미확인) 휠체어 어르신이면 홍천군 등록 신청서 (공식 양식 위에 값 채움)
병원동행(노인복지관 전화 예약)과 희망택시(이장이 전화로 요청)는 주민용 공식 신청서가 없어 PDF에 넣지 않는다.

칸 위치는 양식의 글자 위치(PyMuPDF search_for)로 찾은 좌표다. 양식 원본은 app/forms/README.md 참고.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf as fitz

from .workflow import elder_name, form_address

HERE = Path(__file__).resolve().parent
FORMS = HERE / "forms"
FONT = str(HERE / "static" / "fonts" / "Paperlogy-4Regular.ttf")
FONT_BOLD = str(HERE / "static" / "fonts" / "Paperlogy-7Bold.ttf")
INK = (0.05, 0.25, 0.75)  # 채운 값은 파란 글씨로 구분한다
GRAY = (0.45, 0.48, 0.52)


def _fonts(page: fitz.Page) -> None:
    page.insert_font(fontname="P", fontfile=FONT)
    page.insert_font(fontname="PB", fontfile=FONT_BOLD)


def _put(page: fitz.Page, x: float, y: float, text: str | None, size: float = 10, color=INK,
         width: float | None = None) -> None:
    if text:
        if width:  # 칸보다 길면 글자를 줄여 칸 안에 넣는다(긴 주소)
            size = min(size, size * width / fitz.Font(fontfile=FONT).text_length(str(text), fontsize=size))
        page.insert_text((x, y), str(text), fontname="P", fontsize=size, color=color)


def _check(page: fitz.Page, x: float, y: float) -> None:
    page.insert_text((x, y), "√", fontname="PB", fontsize=11, color=INK)


def _addr(req: dict) -> str:
    return form_address(req.get("pickup") or {})  # 대시보드 주소 칸과 같은 주소


def _day(req: dict) -> date:
    return date.fromisoformat((req.get("created_at") or date.today().isoformat())[:10])


def _phone(req: dict) -> str:
    return (req.get("call") or {}).get("caller") or ""


def integrated_form(doc: fitz.Document, req: dict) -> None:
    """통합지원 신청서(시행규칙 별지 제1호서식) 앞쪽에 값을 채운다. 뒤쪽 동의서는 어르신 서명이 필요해 비워 둔다."""
    src = fitz.open(FORMS / "통합지원_신청서.pdf")
    start = doc.page_count
    doc.insert_pdf(src)
    p = doc[start]
    _fonts(p)
    res = req.get("resident") or {}
    d = _day(req)
    _put(p, 100, 119, req["id"])
    _put(p, 238, 119, f"{d.year}. {d.month}. {d.day}.")
    _put(p, 123, 172, elder_name(req) or "확인 필요", 12)
    _put(p, 430, 171, _phone(req))
    _put(p, 150, 198, _addr(req), width=400)
    _check(p, 214, 549)   # 안내대상: 대상자 본인
    _check(p, 342, 569)   # 안내방식: 문자메시지서비스(SMS)
    _check(p, 324, 685)   # 신청 구분: 본인(전화로 신청)
    _put(p, 424, 722, str(d.year))
    _put(p, 472, 722, str(d.month))
    _put(p, 504, 722, str(d.day))
    _put(p, 360, 738, elder_name(req) or "")
    _put(p, 61, 800, "※ 파란 글씨는 '한 통이면 돼유'가 어르신 전화 내용으로 미리 채운 값입니다. 담당 공무원이 확인한 뒤 서명합니다.",
         7.5, GRAY)


def special_transport_form(doc: fitz.Document, req: dict) -> None:
    """특별교통수단 등 이용대상자 등록 신청서(홍천군 조례 별지 제1호서식)에 값을 채운다."""
    src = fitz.open(FORMS / "특별교통수단_등록신청서.pdf")
    start = doc.page_count
    doc.insert_pdf(src)
    res = req.get("resident") or {}
    d = _day(req)
    name, sex, addr, phone = elder_name(req) or "확인 필요", res.get("sex") or "", _addr(req), _phone(req)
    p = doc[start]
    _fonts(p)
    _put(p, 135, 212, req["id"])
    for y in (331, 423):  # 신청인(본인), 이용대상자 성명
        _put(p, 120, y, name)
        _put(p, 455, y, sex)
    _put(p, 420, 355, "본인")
    for y, w in ((355, 234), (447, 415)):  # 신청인 주소 칸은 '관계' 칸 앞에서 끝난다
        _put(p, 120, y, addr, 9, width=w)
    _put(p, 335, 378, phone)
    _put(p, 335, 470, phone)
    if (res.get("age") or 0) >= 65:
        _check(p, 300, 516)  # 65세 이상 고령자
    _put(p, 123, 662, "휠체어 필요(전동·수동 확인)", 8)
    p2 = doc[start + 1]
    _fonts(p2)
    p2.draw_rect(fitz.Rect(270, 125, 318, 141), color=None, fill=(1, 1, 1))
    _put(p2, 245, 137, f"{d.year}년 {d.month}월 {d.day}일", 11)
    _put(p2, 135, 180, name, 11)
    p4 = doc[start + 3]
    _fonts(p4)
    p4.draw_rect(fitz.Rect(276, 149, 320, 163), color=None, fill=(1, 1, 1))
    _put(p4, 252, 160, f"{d.year}년 {d.month}월 {d.day}일", 11)
    _put(p4, 340, 200, name, 11)


FORMS_BY_KIND = {"integrated": ("통합지원 신청서", integrated_form),
                 "special_transport": ("특별교통수단 등록 신청서", special_transport_form)}


def needs_registration(req: dict) -> bool:
    """휠체어 차량이 필요한데 특별교통수단 등록이 안 됐거나 모르면 등록 신청서가 필요하다."""
    return any(a["type"] == "special_transport" for a in req.get("applications") or []) and \
        (req.get("resident") or {}).get("special_transport") is not True


def single(req: dict, kind: str) -> bytes:
    """공식 신청서 한 장(통합지원 또는 특별교통수단 등록)에 값을 채운 PDF."""
    doc = fitz.open()
    FORMS_BY_KIND[kind][1](doc, req)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
