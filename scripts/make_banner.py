"""README 배너(글꼴 Paperlogy): 전화기 로고 + '한 통이면 돼유' + 부제 + 한 줄 특징. GitHub 밝은·어두운 테마용 투명 PNG 두 장.
  python scripts/make_banner.py → docs/banner-light.png, docs/banner-dark.png (Playwright Chromium으로 그린다)
  브라우저를 따로 받지 않으려면 CHROME=<크롬 실행 파일 경로>를 준다."""
import os
from base64 import b64encode
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "dashboard" / "dist" / "fonts"
THEMES = {  # 제목 그라데이션(남색 → 파랑), 부제, 특징, 로고 바탕, 로고 배지(주황)
    "light": {"g1": "#284488", "g2": "#3F74E0", "sub": "#4A5468", "tag": "#5E687C", "disc": "#284488", "dot": "#E07B00"},
    "dark": {"g1": "#8FAEF5", "g2": "#B9CCFF", "sub": "#C3CAD8", "tag": "#9AA4B8", "disc": "#3A5BAE", "dot": "#F0952A"},
}
# 전화기 모양: Feather Icons 'phone' (MIT)
PHONE = ("M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67"
         "A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6"
         "l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z")
LOGO = """<svg width="230" height="230" viewBox="0 0 230 230" xmlns="http://www.w3.org/2000/svg">
  <circle cx="108" cy="122" r="100" fill="{disc}"/>
  <g transform="translate(52,66) scale(4.7)" fill="none" stroke="#FFFFFF" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="{phone}"/></g>
  <circle cx="186" cy="46" r="38" fill="{dot}"/>
  <text x="186" y="61" text-anchor="middle" font-family="Paperlogy" font-weight="700" font-size="44" fill="#FFFFFF">1</text>
</svg>"""


def html(t):
    faces = "".join(
        f"@font-face{{font-family:'Paperlogy';src:url('data:font/woff2;base64,{b64encode((FONTS / f'Paperlogy-{w}.woff2').read_bytes()).decode()}') format('woff2');font-weight:{n}}}"
        for w, n in (("4Regular", 400), ("6SemiBold", 600), ("7Bold", 700)))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{faces}
html,body{{margin:0;background:transparent}}
#b{{display:flex;align-items:center;gap:44px;padding:28px 40px;width:max-content}}
h1{{margin:0;font:700 140px/1 'Paperlogy',sans-serif;letter-spacing:-2px;background:linear-gradient(90deg,{t['g1']},{t['g2']});
   -webkit-background-clip:text;background-clip:text;color:transparent;padding-bottom:12px}}
.sub{{font:600 40px/1.3 'Paperlogy',sans-serif;color:{t['sub']};margin-top:8px}}
.tag{{font:400 27px/1.4 'Paperlogy',sans-serif;color:{t['tag']};margin-top:12px}}
</style></head><body><div id="b">{LOGO.format(phone=PHONE, **t)}<div><h1>한 통이면 돼유</h1>
<div class="sub">어르신 병원 가는 날, 전화 한 통으로 신청하는 AI 상담원</div>
<div class="tag">홍천 공공데이터 · 통화 중 확인 · 담당 공무원 승인 · 공식 신청서 자동 작성</div></div></div></body></html>"""


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None,
                                args=["--no-sandbox", "--no-zygote", "--disable-gpu"])
    page = browser.new_page(viewport={"width": 1700, "height": 500}, device_scale_factor=2)
    for name, t in THEMES.items():
        page.set_content(html(t))
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(300)
        assert page.evaluate("[...document.fonts].every(f => f.status === 'loaded')"), "Paperlogy를 불러오지 못함"
        page.locator("#b").screenshot(path=str(ROOT / "docs" / f"banner-{name}.png"), omit_background=True)
    browser.close()
print("docs/banner-light.png · docs/banner-dark.png")
