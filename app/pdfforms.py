"""신청 한 건을 기관에 낼 PDF 묶음으로 만든다.

1. 통합지원 신청서 (공식 양식 위에 값 채움)
2. 기관별 신청서 (병원동행·희망택시·특별교통수단: 주민용 공식 양식이 없어 우리 양식)
3. 특별교통수단 미등록(또는 미확인) 휠체어 어르신이면 홍천군 등록 신청서 (공식 양식 위에 값 채움)

칸 위치는 양식의 글자 위치(PyMuPDF search_for)로 찾은 좌표다. 양식 원본은 app/forms/README.md 참고.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf as fitz

HERE = Path(__file__).resolve().parent
FORMS = HERE / "forms"
FONT = str(HERE / "static" / "fonts" / "Paperlogy-4Regular.ttf")
FONT_BOLD = str(HERE / "static" / "fonts" / "Paperlogy-7Bold.ttf")
INK = (0.05, 0.25, 0.75)  # 채운 값은 파란 글씨로 구분한다
GRAY = (0.45, 0.48, 0.52)


def _fonts(page: fitz.Page) -> None:
    page.insert_font(fontname="P", fontfile=FONT)
    page.insert_font(fontname="PB", fontfile=FONT_BOLD)


def _put(page: fitz.Page, x: float, y: float, text: str | None, size: float = 10, color=INK) -> None:
    if text:
        page.insert_text((x, y), str(text), fontname="P", fontsize=size, color=color)


def _check(page: fitz.Page, x: float, y: float) -> None:
    page.insert_text((x, y), "√", fontname="PB", fontsize=11, color=INK)


def _addr(req: dict) -> str:
    p = req.get("pickup") or {}
    base = f"강원특별자치도 홍천군 {p['village']}" if p.get("village") else "강원특별자치도 홍천군 (마을 확인 필요)"
    return base + (f" ({p['detail']})" if p.get("detail") else "")


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
    _put(p, 123, 172, res.get("name") or "확인 필요", 12)
    _put(p, 430, 171, _phone(req))
    _put(p, 150, 198, _addr(req))
    _check(p, 214, 549)   # 안내대상: 대상자 본인
    _check(p, 342, 569)   # 안내방식: 문자메시지서비스(SMS)
    _check(p, 324, 685)   # 신청 구분: 본인(전화로 신청)
    _put(p, 424, 722, str(d.year))
    _put(p, 472, 722, str(d.month))
    _put(p, 504, 722, str(d.day))
    _put(p, 360, 738, res.get("name") or "")
    _put(p, 61, 800, "※ 파란 글씨는 '한 통이면 돼유'가 어르신 전화 내용으로 미리 채운 값입니다. 담당 공무원이 확인한 뒤 서명합니다.",
         7.5, GRAY)


def special_transport_form(doc: fitz.Document, req: dict) -> None:
    """특별교통수단 등 이용대상자 등록 신청서(홍천군 조례 별지 제1호서식)에 값을 채운다."""
    src = fitz.open(FORMS / "특별교통수단_등록신청서.pdf")
    start = doc.page_count
    doc.insert_pdf(src)
    res = req.get("resident") or {}
    d = _day(req)
    name, sex, addr, phone = res.get("name") or "확인 필요", res.get("sex") or "", _addr(req), _phone(req)
    p = doc[start]
    _fonts(p)
    _put(p, 135, 212, req["id"])
    for y in (331, 423):  # 신청인(본인), 이용대상자 성명
        _put(p, 120, y, name)
        _put(p, 455, y, sex)
    _put(p, 420, 355, "본인")
    for y in (355, 447):
        _put(p, 120, y, addr, 9)
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


def agency_form(doc: fitz.Document, req: dict, app: dict) -> None:
    """공식 주민용 양식이 없는 기관 신청서를 한 장으로 그린다."""
    page = doc.new_page(width=595, height=842)
    _fonts(page)
    font = fitz.Font(fontfile=FONT)
    page.insert_text((50, 70), app["label"], fontname="PB", fontsize=20)
    page.insert_text((50, 92), f"제출처: {app['agency']} ({app['contact']})", fontname="P", fontsize=11, color=GRAY)
    page.insert_text((50, 110), f"접수번호 {req['id']}-{app['id']} · 접수일 {_day(req).isoformat()} · 작성: 한 통이면 돼유(AI 초안)",
                     fontname="P", fontsize=9, color=GRAY)
    page.draw_line((50, 120), (545, 120), color=(0.12, 0.13, 0.14), width=1.2)

    y, left, mid, right = 132, 50, 190, 545
    rows = list((app.get("fields") or {}).items())
    for k, v in rows:
        v = str(v)
        width = right - mid - 16
        lines = max(1, int(font.text_length(v, 11) // width) + 1 + v.count("\n"))
        h = 12 + 15 * lines
        page.draw_rect(fitz.Rect(left, y, mid, y + h), color=(0.6, 0.64, 0.68), fill=(0.96, 0.96, 0.97), width=0.6)
        page.draw_rect(fitz.Rect(mid, y, right, y + h), color=(0.6, 0.64, 0.68), width=0.6)
        page.insert_textbox(fitz.Rect(left + 8, y + 6, mid - 6, y + h), k, fontname="PB", fontsize=11)
        page.insert_textbox(fitz.Rect(mid + 8, y + 6, right - 8, y + h + 4), v, fontname="P", fontsize=11, color=INK)
        y += h
    notes = app.get("notes") or []
    if notes:
        y += 18
        page.insert_text((50, y), "담당자 확인 사항", fontname="PB", fontsize=12)
        for n in notes:
            y += 6
            r = fitz.Rect(58, y, right, y + 15 * (int(font.text_length(n, 10) // (right - 70)) + 1) + 4)
            page.insert_textbox(r, "· " + n, fontname="P", fontsize=10, color=(0.25, 0.27, 0.3))
            y = r.y1
    y = max(y + 30, 690)
    for i, label in enumerate(("읍·면 담당자 확인", "기관 접수 확인")):
        box = fitz.Rect(50 + i * 252, y, 293 + i * 252, y + 70)
        page.draw_rect(box, color=(0.6, 0.64, 0.68), width=0.6)
        page.insert_text((box.x0 + 10, box.y0 + 20), label, fontname="P", fontsize=10)
        page.insert_text((box.x0 + 10, box.y0 + 55), "성명:                       (서명)", fontname="P", fontsize=10, color=GRAY)
    page.insert_text((50, 800), "이 신청서는 어르신 전화 내용을 AI가 정리한 초안입니다. 담당 공무원이 확인한 뒤 제출합니다. "
                     "AI는 진단·자격 판정을 하지 않습니다.", fontname="P", fontsize=7.5, color=GRAY)


def bundle(req: dict) -> bytes:
    doc = fitz.open()
    integrated_form(doc, req)
    apps = req.get("applications") or []
    for app in apps:
        agency_form(doc, req, app)
    needs_registration = any(a["type"] == "special_transport" for a in apps) and \
        (req.get("resident") or {}).get("special_transport") is not True
    if needs_registration:
        special_transport_form(doc, req)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
