"""데모 화면 — streamlit run app.py

답변만 보여 주면 이 에이전트가 무엇을 한 건지 알 수 없다.
답과 함께 ① 탄 경로 ② 근거 삼중항 ③ 출처 문서를 나란히 둔다.
사용자가 답을 믿을지 말지 스스로 판단할 재료를 주는 것이 목적이다.
"""
import json
import os

# 어디서 실행하든 이 파일이 있는 폴더를 기준으로 데이터를 찾는다.
# streamlit 을 상위 폴더에서 띄우면 상대 경로가 조용히 어긋난다.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(page_title="체류자격 GraphRAG", page_icon="🛂", layout="wide")

# streamlit 기본값이 접근성 기준을 못 넘기는 곳만 덮어쓴다. 취향이 아니라 대비비 문제다.
# `impeccable detect` 가 잡아낸 것: caption 이 1.1:1 (기준 4.5:1), 사이드바 하단 여백 없음.
# 캡션은 이 화면에서 면책 고지와 출처 설명을 담고 있어서, 안 보이면 안 되는 글이다.
st.markdown("""
<style>
  /* 기본 caption 은 opacity 로 흐려 놓아 대비가 1.1:1 까지 떨어진다 */
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
      color: #4b5563 !important;   /* 흰 배경 대비 7.56:1 */
      opacity: 1 !important;
  }
  /* 사이드바 내용이 아래 경계에 붙는다 */
  section[data-testid="stSidebar"] > div,
  [data-testid="stSidebarContent"],
  [data-testid="stSidebarUserContent"] { padding-bottom: 2.5rem; }
  /* 근거 삼중항처럼 줄이 긴 코드 블록은 조금 작게, 줄간격은 넓게 */
  pre code { font-size: 0.82rem; line-height: 1.6; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def boot():
    import agent
    return agent


@st.cache_data
def corpus_stats():
    m = json.load(open("data/manifest.json", encoding="utf-8"))
    c = json.load(open("output/communities.json", encoding="utf-8"))
    return len(m["docs"]), c


st.title("체류자격 GraphRAG 에이전트")
st.caption("한국에 사는 외국인의 비자·정착 질문에, 공식 안내문과 법령만 근거로 답합니다. "
           "답과 함께 그래프에서 **실제로 걸어간 경로**를 보여 줍니다.")

EXAMPLES = [
    ("멀티홉", "E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?"),
    ("멀티홉", "H-2(방문취업)인데 영주자격을 받으려면 무엇이 필요한가요?"),
    ("멀티홉", "어학연수(D-4)를 마치고 대학에 가려면 비자를 어떻게 해야 하나요?"),
    ("단순 조회", "외국인등록은 어디에 신청하나요?"),
    ("전역", "이 자료에는 외국인의 체류와 정착에 관해 어떤 갈래의 제도가 있나요?"),
    ("모르는 것", "베트남 국적자는 F-2 점수가 몇 점 필요한가요?"),
]

with st.sidebar:
    n_docs, comms = corpus_stats()
    st.subheader("무엇으로 답하나")
    st.metric("근거 문서", "%d건" % n_docs)
    st.caption("하이코리아 공식 안내 + 출입국관리법·시행령·국적법 조문과 별표. "
               "법령 본문은 저작권 보호 대상이 아니고, 안내문은 공공누리 자료입니다.")
    st.subheader("자료 안의 제도 갈래")
    for c in comms[:8]:
        if c["size"] >= 4:
            st.write("· **%s** (%d개)" % (c["title"], c["size"]))
    st.divider()
    st.caption("이 답변은 공식 상담이 아닙니다. 실제 신청 전에는 1345 또는 "
               "관할 출입국·외국인청에 확인하세요.")

st.write("**예시 질문**")
cols = st.columns(3)
picked = None
for i, (tag, q) in enumerate(EXAMPLES):
    if cols[i % 3].button("%s · %s" % (tag, q[:22] + "…"), key="ex%d" % i,
                          use_container_width=True):
        picked = q

# 예시 버튼이 눌리면 입력칸에 **써 넣는다**. st.text_input 의 `value=` 는 최초 1회만
# 반영되므로, 그것만 믿으면 버튼을 눌러도 칸이 빈 채로 남아 아무 일도 일어나지 않는다.
# 실제로 그랬다 — 버튼이 전부 죽어 있었는데 오류도 안 났다.
if picked:
    st.session_state["q"] = picked

# 주소에 ?q=... 로 질문을 실어 올 수 있다. 링크로 답을 공유할 수 있고,
# 헤드리스 브라우저로 화면을 캡처할 때도 이 경로가 필요하다
# (헤드리스는 버튼을 누를 수 없다).
_url_q = st.query_params.get("q")
if _url_q and "q" not in st.session_state:
    st.session_state["q"] = _url_q

q = st.text_input("질문", key="q",
                  placeholder="예: E-9 비자인데 영주권까지 갈 수 있나요?")
go = st.button("물어보기", type="primary") or bool(picked) or bool(_url_q)

if go and q.strip():
    agent = boot()
    with st.spinner("그래프를 걸어가는 중…"):
        r = agent.ask(q.strip())

    route_kr = {"local": "단순 조회 (1홉)", "path": "멀티홉 탐색", "global": "전역 요약 (커뮤니티)"}
    st.info("경로 유형: **%s**  ·  시작 개체: %s"
            % (route_kr.get(r["route"], r["route"]),
               ", ".join(r["seeds"]) if r["seeds"] else "없음"))

    st.subheader("답변")
    st.write(r["answer"])
    for f in r["flags"]:
        st.warning("검증 단계가 고친 것 — %s" % f)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("걸어간 경로")
        if r["path"]:
            st.code("\n".join("(%s) --[%s]--> (%s)" % (h, rel, t) for h, rel, t in r["path"]),
                    language=None)
        else:
            st.caption("경로 없음 — 근거를 찾지 못했습니다.")
    with right:
        st.subheader("출처 문서")
        if r["sources"]:
            for s in r["sources"]:
                st.write("· " + s)
        else:
            st.caption("없음")

    with st.expander("근거 삼중항 %d개 (원본)" % len(r["evidence"])):
        for h, rel, t, src, hop in r["evidence"]:
            s = src if isinstance(src, str) else "; ".join(src)
            st.write("`%d홉` **(%s)** —[%s]→ **(%s)**  · %s" % (hop, h, rel, t, s))

    # 로컬에서 돌린 기록을 남긴다. 평가와 회고에 쓴다.
    os.makedirs("output", exist_ok=True)
    with open("output/runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── 지식 그래프 둘러보기 ────────────────────────────────────────────────────
# 답변만 보면 이 에이전트가 무엇 위에서 도는지 알 수 없다. 그래프를 직접 만져 보면
# "왜 이 답이 나왔는지"가 아니라 "무엇까지 답할 수 있는지"가 보인다.
st.divider()
with st.expander("지식 그래프 둘러보기 — 검색 · 관계 필터 · 척추만 보기"):
    if os.path.exists("output/graph.html"):
        import streamlit.components.v1 as components
        components.html(open("output/graph.html", encoding="utf-8").read(), height=620)
    else:
        st.caption("`python make_views.py` 를 돌리면 생깁니다.")
