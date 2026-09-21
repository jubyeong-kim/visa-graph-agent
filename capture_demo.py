"""데모 화면을 파일로 찍는다. `python capture_demo.py`

`docs/demo.png` 는 README·REPORT 가 참조하므로 손으로 찍으면 코드가 바뀔 때마다
낡는다. 스크립트로 만들어 두면 다시 찍는 것이 한 줄이다.

헤드리스 크롬의 `--screenshot` 은 쓸 수 없다. Streamlit 은 웹소켓으로 화면을 그려서
`--virtual-time-budget` 으로 기다려도 **빈 화면이 찍힌다**(실제로 그랬다).
답변이 뜰 때까지 **요소를 기다릴 수 있어야** 한다.

브라우저는 이미 깔린 크롬을 쓴다(`channel="chrome"`). playwright 의 크로미움을
따로 내려받지 않는다.
"""
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

PORT = 8523
Q = (sys.argv[1] if len(sys.argv) > 1
     else "E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?")
OUT = "docs/demo.png"
URL = "http://127.0.0.1:%d/?q=%s" % (PORT, urllib.parse.quote(Q))

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    # Streamlit 은 본문을 **자체 스크롤 컨테이너**에 담는다. 그래서 full_page 를 켜도
    # 문서 높이가 안 늘어나 아래쪽(걸어간 경로·출처)이 잘린다. 화면을 세로로 크게
    # 잡아 스크롤 없이 한 번에 들어가게 하는 쪽이 확실하다.
    pg = b.new_page(viewport={"width": 1400, "height": 1750}, device_scale_factor=2)
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    # 답변이 다 그려질 때까지 기다린다.
    #
    # 함정: 처음엔 "걸어간 경로" 를 기다렸는데 **소개 문구에도 그 말이 있어서**
    # ("…실제로 걸어간 경로를 보여 줍니다") 대기가 즉시 통과했고, 로딩 중 화면이 찍혔다.
    # 답이 나왔을 때만 생기는 것을 기다려야 한다 — 스피너가 사라지고 「출처 문서」가 뜰 때.
    pg.wait_for_selector("[data-testid='stSpinner']", state="detached", timeout=180000)
    pg.get_by_text("출처 문서", exact=True).wait_for(timeout=180000)
    # 「걸어간 경로」는 st.code 라 문법 하이라이터가 **나중에** 붙는다. 여기서
    # 2초만 세고 찍었더니 경로 칸이 빈 회색 상자로 찍혔다 — 화면은 멀쩡했는데
    # 그림만 비어 있었다. 글자가 실제로 들어올 때까지 기다린다.
    pg.wait_for_function(
        "(document.querySelector('[data-testid=\\'stCode\\'] code')||{}).textContent"
        "?.includes('--[') === true", timeout=60000)
    pg.wait_for_timeout(1500)                 # 마지막 페인트
    # 찍은 뒤에 화면 크기를 바꾸면 **Streamlit 이 다시 실행되어 답이 날아간다**.
    # (실제로 그랬다 — 리사이즈 후 답변이 사라진 채로 찍혔다.)
    # 크기는 처음에 정하고 끝까지 건드리지 않는다.
    # 본문이 끝나는 높이까지만 잘라 찍는다. 아래 흰 여백이 절반을 차지하면
    # README 에서 그림이 작게 보인다. viewport 를 바꾸면 Streamlit 이 다시 실행되므로
    # 크기는 그대로 두고 clip 으로 잘라낸다.
    last = pg.locator("[data-testid='stExpander']").last
    box = last.bounding_box()
    height = int(box["y"] + box["height"] + 24) if box else 1750
    pg.screenshot(path=OUT, clip={"x": 0, "y": 0, "width": 1400,
                                  "height": min(height, 1750)})
    b.close()

print("→ %s" % OUT)
print("   질문: %s" % Q)
