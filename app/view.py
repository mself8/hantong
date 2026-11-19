"""대시보드가 나오기 전에 신청서와 안내문을 눈으로 확인하는 읽기 전용 화면."""
from __future__ import annotations

from html import escape

from . import store
from .workflow import APP_STATUSES

STATUS = {"processing": "정리 중", "pending": "확인 대기", "submitted": "기관 회신 대기",
          "confirmed": "안내 대기", "notified": "안내 완료", "held": "보류"}

CSS = """
@font-face { font-family:"Paperlogy"; src:url("/fonts/Paperlogy-4Regular.woff2") format("woff2"); font-weight:400; }
@font-face { font-family:"Paperlogy"; src:url("/fonts/Paperlogy-7Bold.woff2") format("woff2"); font-weight:700; }
:root { --ink:#1d2433; --muted:#5b6475; --bg:#f6f4ef; --card:#fff; --line:#e3dfd6; --warn:#b3261e; --ok:#1f7a45; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font-family:"Paperlogy","Apple SD Gothic Neo","Malgun Gothic",sans-serif; word-break:keep-all; font-size:15px; line-height:1.55; }
main { max-width:960px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:22px; margin:0 0 4px; } .lead { color:var(--muted); margin:0 0 20px; }
.req { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:18px; }
.head { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:6px; }
.badge { font-size:12px; font-weight:700; padding:2px 8px; border-radius:999px; background:#eef0f4; color:var(--ink); }
.badge.pending { background:#fff3cd; } .badge.submitted { background:#e2ecff; } .badge.confirmed { background:#e3f4e8; }
.badge.notified { background:#d7f0df; color:var(--ok); }
.summary { font-size:17px; font-weight:700; } .meta { color:var(--muted); font-size:13px; }
.checks { margin:10px 0 0; padding-left:18px; } .checks .warn { color:var(--warn); } .checks .info { color:var(--muted); }
h3 { font-size:15px; margin:16px 0 8px; }
.app { border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; }
.app .title { font-weight:700; } .app .to { color:var(--muted); font-size:13px; }
table { border-collapse:collapse; width:100%; margin-top:8px; }
td { border-top:1px solid var(--line); padding:5px 6px; vertical-align:top; }
td:first-child { color:var(--muted); width:30%; white-space:nowrap; }
.notes { margin:8px 0 0; padding-left:18px; color:var(--warn); font-size:14px; }
.msg { white-space:pre-wrap; background:#fbfaf6; border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
details { margin-top:12px; } summary { cursor:pointer; color:var(--muted); }
.turn { margin:4px 0; } .turn b { display:inline-block; width:52px; color:var(--muted); font-weight:600; }
a { color:#2554c7; }
@media (max-width:600px) { td:first-child { width:38%; white-space:normal; } }
"""


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _request_html(r: dict) -> str:
    res = r.get("resident") or {}
    who = f"{res.get('name')} ({res.get('village')})" if res else "명부 확인 필요"
    st = r.get("status", "")
    parts = [f"<section class='req'><div class='head'><span class='badge {st}'>{_e(STATUS.get(st, st))}</span>"
             f"<span class='summary'>{_e(r.get('summary') or '정리 중')}</span></div>"
             f"<div class='meta'>{_e(r['id'])} · 받은 시각 {_e(r.get('created_at', '')[:16].replace('T', ' '))} · {_e(who)}"
             f" · <a href='/api/requests/{_e(r['id'])}'>JSON</a></div>"]
    checks = r.get("checks") or []
    if checks:
        parts.append("<ul class='checks'>" + "".join(
            f"<li class='{_e(c.get('level'))}'>{_e(c.get('message'))}</li>" for c in checks) + "</ul>")

    apps = r.get("applications") or []
    parts.append("<h3>기관별 신청서</h3>")
    if not apps:
        parts.append("<p class='meta'>기관에 낼 신청서가 없는 건이에요.</p>")
    for a in apps:
        rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in (a.get("fields") or {}).items())
        notes = "".join(f"<li>{_e(n)}</li>" for n in a.get("notes") or [])
        resp = a.get("response") or {}
        parts.append(f"<div class='app'><span class='title'>{_e(a['label'])}</span> "
                     f"<span class='badge'>{_e(APP_STATUSES.get(a['status'], a['status']))}</span>"
                     f"<div class='to'>보낼 곳: {_e(a['agency'])} ({_e(a['contact'])})</div>"
                     f"<table>{rows}</table>"
                     + (f"<ul class='notes'>{notes}</ul>" if notes else "")
                     + (f"<div class='to'>기관 응답: {_e(resp.get('detail') or resp.get('result'))}</div>" if resp else "")
                     + "</div>")

    msg = r.get("message")
    parts.append("<h3>어르신께 보낼 안내문</h3>")
    if msg and msg.get("text"):
        state = "보냄" if msg.get("status") == "sent" else "초안"
        parts.append(f"<div class='meta'>{state}</div><div class='msg'>{_e(msg['text'])}</div>")
    else:
        parts.append("<p class='meta'>기관이 모두 답하면 자동으로 만들어져요. 기관 신청이 필요 없는 건은 바로 만들어져요.</p>")

    turns = r.get("transcript") or []
    if turns:
        lines = "".join(f"<div class='turn'><b>{'어르신' if t['role'] == 'elder' else 'AI'}</b>{_e(t['text'])}</div>"
                        for t in turns)
        parts.append(f"<details><summary>통화 기록 ({len(turns)}줄)</summary>{lines}</details>")
    parts.append("</section>")
    return "".join(parts)


def page(limit: int = 30) -> str:
    reqs = store.list_all(None)[:limit]
    body = "".join(_request_html(r) for r in reqs) or "<p class='meta'>아직 들어온 신청이 없어요.</p>"
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>한 통이면 돼유 · 확인용 화면</title><style>{CSS}</style></head><body><main>"
            "<h1>한 통이면 돼유 · 신청서 확인</h1>"
            "<p class='lead'>대시보드가 나오기 전 확인용 화면이에요. 새로고침하면 최신 신청이 보여요.</p>"
            f"{body}</main></body></html>")


FORM_CSS = """
@font-face { font-family:"Paperlogy"; src:url("/fonts/Paperlogy-4Regular.woff2") format("woff2"); font-weight:400; }
@font-face { font-family:"Paperlogy"; src:url("/fonts/Paperlogy-7Bold.woff2") format("woff2"); font-weight:700; }
* { box-sizing:border-box; }
body { margin:0; background:#eceef1; color:#1e2124; font-family:"Paperlogy","Apple SD Gothic Neo",sans-serif; word-break:keep-all; }
.sheet { width:210mm; min-height:297mm; margin:24px auto; background:#fff; padding:20mm 18mm; box-shadow:0 2px 12px rgba(0,0,0,.08); }
h1 { font-size:26px; text-align:center; margin:0 0 6px; letter-spacing:2px; }
.to { text-align:center; color:#464c53; margin:0 0 18px; }
.meta { display:flex; justify-content:space-between; font-size:13px; color:#6d7882; border-bottom:2px solid #1e2124; padding-bottom:8px; }
table { width:100%; border-collapse:collapse; margin-top:14px; font-size:15px; }
th, td { border:1px solid #9aa4ae; padding:10px 12px; text-align:left; vertical-align:top; }
th { width:28%; background:#f4f5f7; font-weight:700; }
h2 { font-size:16px; margin:20px 0 6px; }
ul { margin:0; padding-left:20px; font-size:14px; color:#464c53; line-height:1.7; }
.sign { margin-top:28px; display:grid; grid-template-columns:1fr 1fr; gap:12px; font-size:14px; }
.sign div { border:1px solid #9aa4ae; padding:12px; min-height:64px; }
.foot { margin-top:18px; font-size:12px; color:#6d7882; }
.bar { text-align:center; margin:16px; }
.bar button { font:inherit; font-size:16px; padding:10px 22px; border:0; border-radius:10px; background:#256ef4; color:#fff; cursor:pointer; }
@media print { body { background:#fff; } .sheet { margin:0; box-shadow:none; width:auto; min-height:auto; } .bar { display:none; } }
@page { size:A4; margin:12mm; }
"""


def form_page(req: dict, app: dict) -> str:
    """기관에 내는 신청서 한 장(인쇄용)."""
    rows = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in (app.get("fields") or {}).items())
    notes = "".join(f"<li>{_e(n)}</li>" for n in app.get("notes") or [])
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(app['label'])} · {_e(req['id'])}</title><style>{FORM_CSS}</style></head><body>"
            "<div class='bar'><button onclick='window.print()'>인쇄하기</button></div>"
            f"<div class='sheet'><h1>{_e(app['label'])}</h1>"
            f"<p class='to'>제출처: {_e(app['agency'])} ({_e(app['contact'])})</p>"
            f"<div class='meta'><span>접수번호 {_e(req['id'])}-{_e(app['id'])}</span>"
            f"<span>접수일 {_e(req.get('created_at', '')[:10])}</span>"
            f"<span>상태 {_e(APP_STATUSES.get(app['status'], app['status']))}</span></div>"
            f"<table>{rows}</table>"
            + (f"<h2>담당자 확인 사항</h2><ul>{notes}</ul>" if notes else "")
            + "<div class='sign'><div>읍·면 담당자 확인<br><br>성명:　　　　　　(서명)</div>"
              "<div>기관 접수 확인<br><br>성명:　　　　　　(서명)</div></div>"
              "<p class='foot'>이 신청서는 어르신 전화 내용을 AI가 정리한 초안입니다. 담당 공무원이 확인한 뒤 제출합니다. "
              "AI는 진단·자격 판정을 하지 않습니다.</p></div></body></html>")
