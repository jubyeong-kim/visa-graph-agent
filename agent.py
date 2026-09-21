"""체류자격 GraphRAG 에이전트 — LangGraph.

                    __start__
                        │
                      route ──────────── global ──────┐
                        │ seeds                       │
                    find_seeds                        │
                        │                             │
              ┌──→   expand ──── global ──────────────┤
              │         │                             │
           deepen ←─────┤ 근거 부족                    ↓
              │         │ build                  global_map
              └─────────┤                             │
                        ↓                       global_reduce
                   build_context ←──────────────────┘
                        │
                   synthesize
                        │
                     verify
                        │
                     __end__

expand → global 엣지가 중요하다. 시작 개체는 찾았는데 아무리 넓혀도 근거가 없으면
전역 요약으로 흘려보낸다 — "1홉인 줄 알았는데 사실 전역 질문"인 경우를 구제한다.
deepen 은 최대 2회. 그래도 없으면 빈 근거를 넘겨 "모른다"고 답하게 한다.
"""
import json
import os
import re
import sys
from typing import Any, TypedDict

import networkx as nx
from langgraph.graph import END, START, StateGraph
from rapidfuzz import fuzz

from build_graph import load_env

load_env()
CFG = json.load(open("config.json", encoding="utf-8"))
TRV = CFG["traversal"]
VISA_CODE = re.compile(r"[A-H]\s*-\s*\d{1,2}(?:-\d)?", re.I)

G = nx.read_graphml("output/graph.graphml")
COMMUNITIES = json.load(open("output/communities.json", encoding="utf-8"))
HUBS = set(TRV["hub_blocklist"])


def llm(role="chat"):
    from llm import chat_model
    return chat_model(role)


# ── 조회 계층 ──────────────────────────────────────────────────────────────
# 그래프를 직접 만지지 않고 이 함수들만 쓴다. 그래프는 더럽다(표기 흔들림, 허브).
# 렌즈를 한 겹 씌워 두면 탐색 코드가 그 더러움을 몰라도 된다.
def label(n):
    lb = G.nodes.get(n, {}).get("label", "")
    return f"{n}({lb})" if lb else n


def edge_records(u, v):
    d = G.get_edge_data(u, v) or {}
    vals = list(d.values())
    return vals if vals and isinstance(vals[0], dict) else ([d] if d else [])


def neighbors(n):
    """양방향으로 인접 삼중항을 낸다. 질문은 관계 방향을 안 따지고 들어온다."""
    for _, v in G.out_edges(n):
        for d in edge_records(n, v):
            yield (n, d["relation"], v, d)
    for u, _ in G.in_edges(n):
        for d in edge_records(u, n):
            yield (u, d["relation"], n, d)


VOCAB = CFG["normalize"].get("question_vocab", {})


def find_entities(q):
    """질문에서 시작 개체를 찾는다. 코드 → 정식 명칭 → 느슨한 이름 순.

    사람이 쓰는 말과 문서가 쓰는 말이 다르다. 사용자는 "알바"라고 하고 문서는
    "체류자격외활동"이라고 쓴다. 문서 말을 **덧붙인다** — 원래 말은 지우지 않는다.
    오른쪽 말은 그래프에 실제로 있는 것만 넣었다. 없는 말은 아무 효과가 없다.
    """
    for human, doc in VOCAB.items():
        if human in q and doc not in q:
            q += " " + doc
    seeds = []
    for m in VISA_CODE.finditer(q):
        code = re.sub(r"\s", "", m.group(0)).upper()
        if code in G:
            seeds.append(code)
    # 긴 이름을 먼저 본다. "외국인등록 사항이 바뀌면"에서 `외국인등록`만 집으면
    # 정작 필요한 `외국인등록사항변경신고`에 닿지 못한다.
    named = []
    for n, d in G.nodes(data=True):
        if n in seeds or n in HUBS:
            continue
        lb = d.get("label", "")
        if lb and len(lb) >= 2 and lb in q:
            named.append((len(n), n))
        elif len(n) >= 3 and n in q:
            named.append((len(n), n))
        # 띄어쓰기를 지우고 한 번 더 본다. 질문은 "외국인등록 사항이", 노드는 "외국인등록사항…"
        elif len(n) >= 6 and n.replace(" ", "") in q.replace(" ", ""):
            named.append((len(n), n))
    seeds += [n for _, n in sorted(named, reverse=True)]
    # 이름이 그대로 안 박혀 있어도 잡아야 한다. "외국인등록 사항이 바뀌면 신고할 때"는
    # 노드 `외국인등록사항변경 신고의무` 를 가리키는데, 부분일치 점수로는 더 짧고 정확한
    # `외국인등록`(100점)에 밀려 안 잡혔다. 노드 글자가 질문에 얼마나 들어 있는지로 재면
    # 긴 이름이 제 점수를 받는다. 짧은 이름은 우연히 높게 나오므로 길이로 거른다.
    qs = re.sub(r"\s", "", q)
    extra = []
    for n in G.nodes:
        if n in seeds or n in HUBS or len(re.sub(r"\s", "", n)) < 8:
            continue
        chars = set(re.sub(r"\s", "", n))
        if sum(1 for c in chars if c in qs) / len(chars) >= 0.75:
            extra.append((len(n), n))
    seeds += [n for _, n in sorted(extra, reverse=True)[:2]]

    if not seeds:                      # 그래도 없으면 느슨하게 한 번 더
        best = max(G.nodes, key=lambda n: fuzz.partial_ratio(n, q), default=None)
        if best and fuzz.partial_ratio(best, q) >= 85:
            seeds.append(best)
    return list(dict.fromkeys(seeds))[:5]


# ── State ──────────────────────────────────────────────────────────────────
class S(TypedDict, total=False):
    question: str
    route: str
    seeds: list
    evidence: list          # (h, r, t, source, hop)
    path: list              # 실제로 탄 경로
    depth: int
    deepened: int
    context: str
    answer: str
    flags: list


# ── 노드 ───────────────────────────────────────────────────────────────────
ROUTE_PROMPT = """질문을 셋 중 하나로 분류하라. 한 단어만 답하라.

local  — 특정 대상 하나의 사실을 묻는다. "외국인등록은 어디서 하나요"
path   — 여러 사실을 이어야 답이 나온다. "E-7인데 영주권까지 어떻게 가나요"
global — 자료 전체의 갈래나 구성을 묻는다. "어떤 제도들이 있나요"

질문: {q}
답:"""


def route(state: S) -> S:
    txt = llm().invoke(ROUTE_PROMPT.format(q=state["question"])).content.strip().lower()
    r = "global" if "global" in txt else ("path" if "path" in txt else "local")
    return {"route": r, "depth": 0, "deepened": 0, "evidence": [], "path": [], "flags": []}


def find_seeds(state: S) -> S:
    return {"seeds": find_entities(state["question"])}


SPINE = {"CONVERTS_TO", "REQUIRES", "SATISFIED_BY"}
HUB_DEGREE = TRV.get("hub_degree", 12)


def is_hub(n, seeds):
    """차수가 큰 노드에서는 더 뻗어 나가지 않는다. **도착은 하되 통과는 안 한다.**

    `지방출입국·외국인관서의 장` 같은 기관 노드는 수십 개 절차가 가리킨다. 거기서
    한 발 더 나가면 난민여행증명서처럼 질문과 무관한 절차가 딸려 온다.

    다만 차수만으로 자르면 안 된다. `F-5`(영주)도 차수 20인데, 거기서 못 나가면
    영주 경로가 통째로 끊긴다 — 이 도메인에서 가장 중요한 길이다.
    체류자격과 시작 개체는 차수가 커도 통과시킨다.
    """
    if n in seeds or VISA_CODE.fullmatch(n):
        return False
    return G.degree(n) >= HUB_DEGREE


def relevance(h, r, t, hop, qtokens, seeds):
    """이 삼중항을 근거로 낼 가치. 높을수록 먼저 담는다.

    측정해 보니 재현율은 홉이 깊어질수록 올랐는데(67→100%) 정밀도는 8~9% 에 붙박이고
    근거 수만 9→34개로 불어났다. 3홉 질문에 34개를 퍼와서 3개만 썼다.
    구조만 보고 퍼오면 이렇게 된다 — 질문을 보고 골라야 한다.
    """
    s = 3.0 / hop                                   # 가까울수록 좋다
    if r in SPINE:
        s += 2.0                                    # 멀티홉의 척추
    if h in seeds or t in seeds:
        s += 1.0                                    # 물어본 대상에 직접 붙어 있다
    for n in (h, t):
        lb = G.nodes.get(n, {}).get("label", "")
        if any(w in n or (lb and w in lb) for w in qtokens):
            s += 1.5                                # 질문에 나온 말과 겹친다
            break
    return s


def expand(state: S) -> S:
    """시작 개체에서 n홉까지 걷고, 그중 **골라서** 근거로 낸다.

    걷는 것과 보여 주는 것을 분리한다. 길을 찾으려면 지나가야 하지만, 지나갔다고
    그 노드의 엣지를 전부 근거로 낼 이유는 없다. 차수 20짜리 노드 하나에 닿았다고
    20개를 담으면 생성 모델이 관계없는 삼중항을 읽느라 답을 놓친다 (실제로 h2-6 이
    그랬다 — 재현율 100% 인데 잡음에 묻혀 "자료에 없다"고 답했다).
    """
    budget = TRV["evidence_budget"]
    per_node = TRV.get("per_node_cap", 5)
    total = TRV.get("evidence_cap", 18)
    max_hops = min(TRV["max_hops"], 1 + state.get("deepened", 0) + 1)
    seeds = state.get("seeds", [])
    qtokens = [w for w in re.split(r"[\s,.?!·]+", state["question"]) if len(w) >= 2]

    # ── 걷기: 후보를 모은다 (여기서는 자르지 않는다) ──────────────────────
    cand, seen = [], set()
    frontier = [(s, 0) for s in seeds]
    visited = set(seeds)
    while frontier:
        node, hop = frontier.pop(0)
        if hop >= max_hops:
            continue
        here = 0
        for h, r, t, d in sorted(neighbors(node),
                                 key=lambda e: -relevance(e[0], e[1], e[2],
                                                          hop + 1, qtokens, seeds)):
            nxt = t if h == node else h
            # 허브는 지나가지 않는다. 한 번 들어가면 온 그래프가 딸려 나온다.
            if nxt not in visited and nxt not in HUBS and not is_hub(nxt, seeds):
                visited.add(nxt)
                frontier.append((nxt, hop + 1))
            key = (h, r, t)
            if key in seen or here >= per_node:      # 한 노드가 근거를 독점하지 않게
                continue
            seen.add(key)
            here += 1
            cand.append((relevance(h, r, t, hop + 1, qtokens, seeds),
                         [h, r, t, d.get("sources", d.get("source", "")), hop + 1]))

    # ── 고르기: 관계별 예산 안에서, 점수 높은 것부터 상한까지 ──────────────
    used, evidence = {}, []
    for _score, e in sorted(cand, key=lambda x: -x[0]):
        if len(evidence) >= total:
            break
        cap = budget.get(e[1], budget["_default"])
        if used.get(e[1], 0) >= cap:
            continue
        used[e[1]] = used.get(e[1], 0) + 1
        evidence.append(e)

    path = [[label(h), r, label(t)] for h, r, t, _s, _hop in evidence]
    return {"evidence": evidence, "path": path, "depth": max_hops}


def deepen(state: S) -> S:
    return {"deepened": state.get("deepened", 0) + 1}


def global_map(state: S) -> S:
    """커뮤니티마다 부분 답을 만든다. 요약은 색인 시점에 미리 만들어 뒀다."""
    ev = [["커뮤니티 #%d %s" % (c["id"], c["title"]), "SUMMARY",
           c["summary"].replace("\n", " ")[:400], "커뮤니티 요약", 0]
          for c in COMMUNITIES if c["size"] >= 4]
    return {"evidence": state.get("evidence", []) + ev,
            "path": state.get("path", []) + [["전역", "COMMUNITY", "%d개 무리" % len(ev)]]}


REDUCE_PROMPT = """아래는 지식 그래프에서 찾은 무리별 요약이다.
질문에 답하도록 이것들을 하나로 합쳐라. 무리 제목을 그대로 나열하지 말고,
제도 갈래로 묶어 설명하라. 요약에 없는 내용은 쓰지 마라.

질문: {q}

무리별 요약:
{parts}
"""


def global_reduce(state: S) -> S:
    parts = "\n\n".join("%s\n%s" % (e[0], e[2]) for e in state["evidence"] if e[1] == "SUMMARY")
    if not parts:
        return {"context": ""}
    txt = llm().invoke(REDUCE_PROMPT.format(q=state["question"], parts=parts)).content
    return {"context": "전역 요약:\n" + txt.strip()}


def build_context(state: S) -> S:
    ev = [e for e in state.get("evidence", []) if e[1] != "SUMMARY"]
    if not ev:
        return {"context": state.get("context", "")}
    lines = []
    for h, r, t, src, hop in ev:
        s = src if isinstance(src, str) else "; ".join(src)
        lines.append("(%s) -[%s]-> (%s)   [%d홉, 출처: %s]" % (label(h), r, label(t), hop, s))
    return {"context": (state.get("context", "") + "\n근거 삼중항:\n" + "\n".join(lines)).strip()}


ANSWER_PROMPT = """너는 한국에 사는 외국인의 체류자격 질문에 답한다.

**아래 근거만으로 답하라.** 근거에 없는 숫자(기간·금액·급수)나 사실은 절대 쓰지 마라.
근거로 답할 수 없으면 "제가 가진 자료로는 확인되지 않습니다"라고 말하고,
무엇이 없어서 못 답하는지 한 줄로 알려라.

쉬운 한국어로, 3~5문장으로 답하라. 체류자격은 코드와 이름을 함께 쓴다(예: E-7 특정활동).

질문: {q}

근거:
{ctx}
"""


def synthesize(state: S) -> S:
    ctx = state.get("context", "").strip()
    if not ctx:
        return {"answer": "제가 가진 자료로는 확인되지 않습니다. "
                          "질문에 나온 체류자격이나 절차가 자료에 없습니다."}
    txt = llm().invoke(ANSWER_PROMPT.format(q=state["question"], ctx=ctx)).content
    return {"answer": txt.strip()}


NUM = re.compile(r"\d+\s*(?:년|개월|일|급|단계|원|만원|점)")


def verify(state: S) -> S:
    """근거에 없는 숫자가 답변에 섞였는지 기계로 본다.

    비자 도메인은 "5년"과 "3년"이 완전히 다른 답이다. 모델은 이런 숫자를
    자신 있게 지어낸다. 확신이 아니라 대조로 잡는다.
    """
    ctx = state.get("context", "")
    bad = [n for n in NUM.findall(state.get("answer", ""))
           if re.sub(r"\s", "", n) not in re.sub(r"\s", "", ctx)]
    if not bad:
        return {}
    # 처음에는 "그 부분을 '자료에서 확인되지 않습니다'로 바꿔라"라고 했다가
    # "3년 이상 체류" → "자료에서 확인되지 않습니다 체류하고" 처럼 문장이 망가졌다.
    # 모델이 숫자 자리에 문구를 그대로 끼워 넣는다. 통째로 다시 쓰게 해야 한다.
    fixed = llm().invoke(
        "아래 답변에 근거로 뒷받침되지 않는 숫자 %s 가 들어 있다.\n"
        "**답변 전체를 다시 써라.** 그 숫자가 들어간 문장은 지우고, 대신 마지막에 "
        "'그 밖의 구체적인 수치는 제가 가진 자료로는 확인되지 않습니다.' 한 문장을 붙여라.\n"
        "근거에 있는 사실은 그대로 살린다. 자연스러운 한국어 문장으로 써라.\n\n"
        "답변:\n%s\n\n근거:\n%s"
        % (", ".join(bad), state["answer"], ctx)).content
    return {"answer": fixed.strip(), "flags": state.get("flags", []) + ["근거없는 숫자 재작성: %s" % bad]}


# ── 분기 ───────────────────────────────────────────────────────────────────
def after_route(state: S):
    return "global" if state["route"] == "global" else "seeds"


def after_expand(state: S):
    if state.get("evidence"):
        return "build"
    # 시작 개체를 **아예 못 찾았으면** 전역으로 보내지 않는다. 그냥 모르는 질문이다.
    #
    # 이걸 몰라서 사고가 났다. 외부 질문 "Smart Entry Service란?" 에 근거 0개로
    # "전자 출입국 시스템입니다" 라고 지어냈다. 코퍼스에 없는 내용이다.
    # 전역 요약이 컨텍스트에 들어가면 모델은 `체류신고제도` 같은 추상적인 무리 제목을
    # 근거 삼아 그럴듯한 말을 만든다. expand → global 은 "시작 개체는 찾았는데
    # 그 주변에 근거가 없는" 경우를 구제하려던 것이지, 개체조차 못 찾은 질문을
    # 받아 주려던 게 아니다.
    if not state.get("seeds"):
        return "build"                      # 빈 근거 → synthesize 가 "모른다"
    if state.get("deepened", 0) < TRV["deepen_limit"]:
        return "deepen"
    return "global"


def make_graph():
    g = StateGraph(S)
    for name, fn in [("route", route), ("find_seeds", find_seeds), ("expand", expand),
                     ("deepen", deepen), ("global_map", global_map),
                     ("global_reduce", global_reduce), ("build_context", build_context),
                     ("synthesize", synthesize), ("verify", verify)]:
        g.add_node(name, fn)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", after_route,
                            {"seeds": "find_seeds", "global": "global_map"})
    g.add_edge("find_seeds", "expand")
    g.add_conditional_edges("expand", after_expand,
                            {"build": "build_context", "deepen": "deepen",
                             "global": "global_map"})
    g.add_edge("deepen", "expand")
    g.add_edge("global_map", "global_reduce")
    g.add_edge("global_reduce", "build_context")
    g.add_edge("build_context", "synthesize")
    g.add_edge("synthesize", "verify")
    g.add_edge("verify", END)
    return g.compile()


APP = make_graph()


def split_sources(src):
    items = [src] if isinstance(src, str) else list(src)
    out = []
    for s in items:
        out += [x.strip() for x in str(s).split(";") if x.strip()]
    return out


def key_paths(seeds, answer, evidence):
    """답에 실제로 쓰인 경로만 골라낸다.

    expand 는 반경 안의 삼중항을 다 쓸어 온다. 그걸 그대로 "탄 경로"라고 보여 주면
    근무처변경 같은 무관한 엣지가 섞여 사용자가 무엇을 믿어야 할지 알 수 없다.
    답변 문장에 등장한 노드까지의 최단 경로만 남긴다.
    """
    # 주의: label 이 빈 문자열인 노드가 많다. `"" in answer` 는 언제나 참이라
    # 그냥 쓰면 모든 노드가 "답에 쓰였다"가 되어 경로 추리기가 무의미해진다.
    def mentioned(n):
        lb = G.nodes.get(n, {}).get("label", "")
        return n in answer or (len(lb) >= 2 and lb in answer)

    used = {n for h, r, t, _s, _h2 in evidence for n in (h, t) if mentioned(n)}
    sub = nx.DiGraph()
    for h, r, t, _s, _hop in evidence:
        sub.add_edge(h, t, relation=r)
        sub.add_edge(t, h, relation=r)          # 질문은 방향을 안 따진다
    paths, seen = [], set()
    for s in seeds:
        for goal in used:
            if s == goal or s not in sub or goal not in sub:
                continue
            try:
                p = nx.shortest_path(sub, s, goal)
            except nx.NetworkXNoPath:
                continue
            for i in range(len(p) - 1):
                e = (p[i], sub.edges[p[i], p[i + 1]]["relation"], p[i + 1])
                if e not in seen:
                    seen.add(e)
                    paths.append([label(e[0]), e[1], label(e[2])])
    return paths


def ask(question):
    out = APP.invoke({"question": question})
    ev = [e for e in out.get("evidence", []) if e[1] != "SUMMARY"]
    answer = out.get("answer", "")
    kp = key_paths(out.get("seeds", []), answer, ev)
    return {
        "question": question,
        "route": out.get("route"),
        "seeds": out.get("seeds", []),
        "answer": answer,
        "path": kp or out.get("path", []),        # 핵심 경로가 없으면 수집한 것 전부
        "all_path": out.get("path", []),
        "evidence": ev,
        "sources": sorted({s for e in ev for s in split_sources(e[3])}),
        "flags": out.get("flags", []),
    }


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?"
    r = ask(q)
    print("질문:", r["question"])
    print("경로 유형:", r["route"], "· 시작 개체:", r["seeds"])
    print("")
    print(r["answer"])
    print("")
    print("탄 경로:")
    for h, rel, t in r["path"][:12]:
        print("  (%s) -[%s]-> (%s)" % (h, rel, t))
    print("")
    print("출처:", ", ".join(r["sources"][:6]))
    if r["flags"]:
        print("검증:", r["flags"])
