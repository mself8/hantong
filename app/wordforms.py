"""공식 신청서를 워드(.docx)로 내려준다. 공무원이 워드에서 고쳐 쓸 수 있게.

틀(app/forms/*.docx)은 국가법령정보센터의 한글(HWP) 공식 양식을 워드로 바꾼 것이다
(HWP → ODT: pyhwp, ODF 스키마 검사 생략 → DOCX: LibreOffice). 칸은 표의 줄·칸 번호로 잡되,
칸 이름을 먼저 확인해서 양식이 바뀌면 바로 알 수 있게 한다. 채운 값은 PDF처럼 파란 글씨다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from docx.shared import RGBColor
from docx.table import _Cell

from .pdfforms import _addr, _day, _phone
from .workflow import elder_name

FORMS = Path(__file__).resolve().parent / "forms"
INK = RGBColor(0x0D, 0x40, 0xBF)


def _row(table, i: int) -> list[_Cell]:
    return [_Cell(tc, table) for tc in table.rows[i]._tr.tc_lst]


def _expect(cell: _Cell, label: str) -> None:
    if not re.sub(r"\s+", "", cell.text).startswith(label):
        raise ValueError(f"양식 칸이 예상과 달라요: '{label}' 자리에 '{cell.text[:20]}'")


def _add(cell: _Cell, text, new_line: bool = False) -> None:
    """칸에 값을 파란 글씨로 넣는다. 칸에 이미 글자(칸 이름)가 있으면 띄워 붙이거나(new_line이면) 새 줄에."""
    if not text:
        return
    p = cell.add_paragraph() if new_line else cell.paragraphs[0]
    run = p.add_run(("  " if p.text.strip() else "") + str(text))
    run.font.color.rgb = INK


def _swap(p, old: str, new: str) -> bool:
    """문단 글자 속 old를 new로 바꾼다(글자 조각이 나뉘어 있어도). 바꾼 문단은 첫 조각의 모양을 따른다."""
    if old not in p.text:
        return False
    text = p.text.replace(old, new, 1)
    p.runs[0].text = text
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    return True


def _date(p, d) -> None:
    p.runs[0].text = f"{d.year}년 {d.month}월 {d.day}일"
    p.runs[0].font.color.rgb = INK
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)


def _save(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def integrated(req: dict) -> bytes:
    """통합지원 신청서(시행규칙 별지 제1호서식). 뒤쪽 동의서는 어르신 서명이 필요해 비워 둔다."""
    doc = Document(FORMS / "통합지원_신청서.docx")
    t = doc.tables[0]
    d, name = _day(req), elder_name(req) or "확인 필요"
    r = _row(t, 3)
    _expect(r[0], "접수번호"), _add(r[0], req["id"])
    _expect(r[1], "접수일"), _add(r[1], f"{d.year}. {d.month}. {d.day}.")
    r = _row(t, 5)
    _expect(r[1], "성명"), _add(r[1], name, new_line=True)
    _expect(r[3], "연락처"), _add(r[3], _phone(req), new_line=True)
    r = _row(t, 6)
    _expect(r[1], "주소"), _add(r[1], _addr(req))
    r = _row(t, 19)
    _expect(r[1], "안내대상"), _swap(r[2].paragraphs[0], "[ ]", "[√]")           # 대상자 본인
    r = _row(t, 20)
    _expect(r[1], "안내방식")
    for p in r[2].paragraphs:
        _swap(p, "[ ]문자", "[√]문자")                                          # 문자메시지(SMS)
    for p in _row(t, 25)[0].paragraphs:
        _swap(p, "[ ]본인", "[√]본인")                                          # 신청 구분: 본인(전화로 신청)
    for p in _row(t, 26)[0].paragraphs:
        if re.sub(r"\s+", "", p.text) == "년월일":
            _date(p, d)
    r = _row(t, 27)
    _expect(r[0], "신청인"), _swap(r[1].paragraphs[0], "(서명 또는 인)", f"{name}   (서명 또는 인)")
    return _save(doc)


def special_transport(req: dict) -> bytes:
    """특별교통수단 등 이용대상자 등록 신청서(홍천군 조례 별지 제1호서식). 본인이 신청하는 것으로 채운다."""
    doc = Document(FORMS / "특별교통수단_등록신청서.docx")
    t = doc.tables[0]
    res = req.get("resident") or {}
    d, name, addr, phone = _day(req), elder_name(req) or "확인 필요", _addr(req), _phone(req)
    r = _row(t, 0)
    _expect(r[0], "접수번호"), _add(r[2], req["id"])   # r[1]은 좁은 여백 칸
    for i in (3, 6):                                    # 신청인, 이용대상자(본인)
        r = _row(t, i)
        _expect(r[1], "성명"), _add(r[2], name)
        _expect(r[5], "성별"), _add(r[6], res.get("sex"))
    r = _row(t, 4)
    _expect(r[1], "주소"), _add(r[2], addr)
    _expect(r[3], "관계"), _add(r[4], "본인")
    for i in (5, 8):
        r = _row(t, i)
        _expect(r[3], "휴대폰번호"), _add(r[4], phone)
    r = _row(t, 7)
    _expect(r[1], "주소"), _add(r[2], addr)
    if (res.get("age") or 0) >= 65:
        _swap(_row(t, 9)[3].paragraphs[0], "□", "☑")    # 65세 이상 고령자
    for cell in (_row(t, 15)[0], _row(doc.tables[1], 2)[0]):   # 앞쪽 제출란, 서약서
        for p in cell.paragraphs:
            s = re.sub(r"\s+", "", p.text)
            if s == "년월일":
                _date(p, d)
            elif s.startswith("신청인본인"):
                _swap(p, "본 인", f"본 인  {name}")
            elif s.startswith("신청인성명"):
                _swap(p, "(인)", f"{name}   (인)")
    return _save(doc)


WORD_FORMS = {"integrated": ("통합지원 신청서", integrated),
              "special_transport": ("특별교통수단 등록 신청서", special_transport)}
