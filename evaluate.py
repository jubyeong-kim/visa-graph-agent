"""평가 — 홉수별 채점 + basic RAG(BM25) 대조 + 경로 재현율 + 실패 층 분류.

세 가지를 잰다.
  ① 정답률          LLM 심판이 기대 정답과 대조한다. 심판은 생성 모델과 다른 모델을 쓴다.
  ② 경로 재현·정밀·F1
                     기대 경로의 삼중항이 실제 근거에 몇 개나 들어왔나(재현),
                     그리고 모은 근거 중 몇 개가 쓸모 있었나(정밀).
                     **재현율만 재면 근거를 많이 퍼올릴수록 점수가 오른다.** 그래서 F1 을 함께 낸다.
                     골든셋은 원문에서 썼고 그래프는 표현을 다듬으므로 느슨하게 맞춘다.
                     엄격 일치만 보면 정규화가 잘된 그래프가 오히려 점수를 잃는다.
  ③ 실패 층          틀린 건이 색인·탐색·생성 중 어디서 깨졌는지 가른다.
                     고칠 곳을 지목하지 못하는 점수는 쓸모가 없다.
"""
import glob
import json
import os
import re
import sys

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

import agent
from agent import G, llm

OUT = "output"
GOLD = json.load(open("data/goldenset.json", encoding="utf-8"))


# ── 비교군: basic RAG (BM25) ───────────────────────────────────────────────
def load_docs():
    docs = {}
    for p in sorted(glob.glob("data/docs/*.md")):
        raw = open(p, encoding="utf-8").read()
        docs[raw.split("\n", 1)[0].lstrip("# ").strip()] = raw
    return docs


DOCS = load_docs()
TITLES = list(DOCS)
# 한국어 형태소 분석기는 이 환경에 없고 설치도 무겁다. 공백+2-gram 으로 자른다.
# GraphRAG 쪽도 같은 코퍼스를 쓰므로 비교 자체는 공정하다.
def tok(s):
    s = re.sub(r"[^\w가-힣A-Za-z0-9-]", " ", s)
    words = s.split()
    grams = [w[i:i + 2] for w in words for i in range(max(1, len(w) - 1))]
    return words + grams


BM25 = BM25Okapi([tok(DOCS[t]) for t in TITLES])

BASIC_PROMPT = """아래 문서만 보고 질문에 답하라. 문서에 없으면 모른다고 답하라.
쉬운 한국어로 3~5문장.

질문: {q}

문서:
{ctx}
"""


def basic_rag(q, k=3):
    scores = BM25.get_scores(tok(q))
    top = sorted(range(len(TITLES)), key=lambda i: -scores[i])[:k]
    ctx = "\n\n".join("# %s\n%s" % (TITLES[i], DOCS[TITLES[i]][:1500]) for i in top)
    ans = llm().invoke(BASIC_PROMPT.format(q=q, ctx=ctx)).content.strip()
    return ans, [TITLES[i] for i in top]


# ── ② 경로 재현율 ──────────────────────────────────────────────────────────
def node_match(a, b):
    """골든셋의 표현과 그래프의 표현을 느슨하게 맞춘다.

    골든셋은 원문에서 썼다("5년 이상 계속하여 대한민국에 주소가 있을 것").
    그래프는 정규화를 거쳐 다듬어져 있다("5년 이상 거주").
    골든셋을 그래프에 맞춰 고치면 채점이 자기 자신을 칭찬하게 되므로 그렇게 하지 않는다.

    다만 숫자가 다르면 무조건 불일치다. 이 도메인에서 3년과 5년은 다른 답이다.
    """
    if a == b:
        return True
    if set(re.findall(r"\d+", a)) != set(re.findall(r"\d+", b)):
        return False
    return fuzz.partial_ratio(a, b) >= 80 or fuzz.token_set_ratio(a, b) >= 75


def path_scores(expected, evidence):
    """경로 재현율·정밀도·F1.

    재현율만 재면 **근거를 많이 퍼올릴수록 점수가 오른다.** 극단적으로 그래프 전체를
    근거로 넘기면 재현율 100% 다. 실제로 이 프로젝트가 그 방향으로 새고 있었다 —
    기대 경로 21개에 근거 63개를 제시해 재현율은 높은데 정밀도가 33% 였다.

    정밀도는 "모은 근거 중 기대 경로에 해당하는 비율"로 잰다. 값 자체는 낮게 나온다.
    기대 경로는 최소 사슬만 적고, 주변 맥락은 거기 안 들어가기 때문이다.
    그러니 절대값이 아니라 **설정을 바꿨을 때 어느 쪽으로 움직이는지**로 읽어야 한다.
    근거 예산(config.json 의 evidence_budget)을 조일수록 정밀도가 오르고
    재현율이 떨어진다. 그 맞바꿈을 보는 것이 이 숫자의 쓸모다.
    """
    if not expected:
        return None, None, None, []
    got, missing = 0, []
    for h, r, t in expected:
        hit = any(r == er and node_match(h, eh) and node_match(t, et)
                  for eh, er, et, _s, _hop in evidence)
        if hit:
            got += 1
        else:
            missing.append([h, r, t])
    recall = got / len(expected)
    precision = got / len(evidence) if evidence else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return recall, precision, f1, missing


# ── ① 정답률: LLM 심판 ─────────────────────────────────────────────────────
JUDGE = """너는 채점자다. 답변이 기대 정답과 사실상 같은 내용인지 판정하라.

표현이 달라도 기대 정답의 **핵심 사실**이 답변에 들어 있으면 정답이다.
기대 정답에 없는 내용을 더 말한 것은 그 자체로는 오답이 아니다.
질문은 하나를 물었어도 답변이 관련 사실을 더 붙일 수 있다.

오답인 경우는 셋뿐이다.
  · 핵심 숫자(기간·급수·횟수)가 기대 정답과 다르다
  · 기대 정답의 핵심을 빠뜨렸다
  · 기대 정답과 어긋나는 사실을 말했다
"모른다"고 답한 것은 기대 정답이 실제 내용을 담고 있으면 오답이다.

질문: {q}
기대 정답: {gold}
답변: {ans}

맞으면 O, 틀리면 X 한 글자만 답하라."""


def judge(q, gold, ans):
    v = llm("judge").invoke(
        JUDGE.format(q=q, gold=gold, ans=ans)).content.strip().upper()
    return v.startswith("O")


# ── ③ 실패 층 분류 ─────────────────────────────────────────────────────────
def failure_layer(item, res, recall):
    """틀린 건이 어디서 깨졌는지 가른다.

    색인 — 정답 삼중항이 그래프에 아예 없다 (추출이 못 뽑았다)
    탐색 — 그래프엔 있는데 근거로 안 들어왔다 (반경·예산·허브 차단 문제)
    생성 — 근거는 다 들어왔는데 답이 틀렸다 (프롬프트·모델 문제)
    """
    exp = item["expected_path"]
    if not exp:
        return "생성" if not res["answer"] else "생성"
    in_graph = []
    for h, r, t in exp:
        ok = any(r == d["relation"] and node_match(h, u) and node_match(t, v)
                 for u, v, d in G.edges(data=True))
        in_graph.append(ok)
    if not all(in_graph):
        return "색인"
    if recall is not None and recall < 1.0:
        return "탐색"
    return "생성"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for it in GOLD["items"]:
        if only and it["id"] != only:
            continue
        res = agent.ask(it["q"])
        rec, prec, f1, missing = path_scores(it["expected_path"], res["evidence"])
        g_ok = judge(it["q"], it["expected_answer"], res["answer"])
        b_ans, b_docs = basic_rag(it["q"])
        b_ok = judge(it["q"], it["expected_answer"], b_ans)
        layer = None if g_ok else failure_layer(it, res, rec)
        rows.append({"id": it["id"], "kind": it["kind"], "hops": it["hops"], "q": it["q"],
                     "graph_ok": g_ok, "basic_ok": b_ok,
                     "path_recall": rec, "path_precision": prec, "path_f1": f1,
                     "n_evidence": len(res["evidence"]),
                     "missing_edges": missing, "layer": layer,
                     "graph_answer": res["answer"], "basic_answer": b_ans,
                     "route": res["route"], "seeds": res["seeds"],
                     "path": res["path"], "sources": res["sources"],
                     "basic_docs": b_docs, "flags": res["flags"]})
        mark = "O" if g_ok else "X"
        print("[%s] %s %d홉 | GraphRAG %s · BM25 %s | 경로 재현 %s 정밀 %s F1 %s | 근거 %d개%s"
              % (it["id"], it["kind"], it["hops"], mark, "O" if b_ok else "X",
                 "-" if rec is None else "%.0f%%" % (rec * 100),
                 "-" if prec is None else "%.0f%%" % (prec * 100),
                 "-" if f1 is None else "%.2f" % f1,
                 len(res["evidence"]),
                 "" if g_ok else " · 실패층 " + layer))

    os.makedirs(OUT, exist_ok=True)
    json.dump(rows, open(os.path.join(OUT, "eval.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("")
    print("=" * 62)
    print("%-8s %5s %9s %7s %8s %8s %6s %7s"
          % ("홉", "문항", "GraphRAG", "BM25", "경로재현", "경로정밀", "F1", "근거수"))
    for hop in sorted({r["hops"] for r in rows}):
        sub = [r for r in rows if r["hops"] == hop]
        recs = [r["path_recall"] for r in sub if r["path_recall"] is not None]
        pres = [r["path_precision"] for r in sub if r["path_precision"] is not None]
        f1s = [r["path_f1"] for r in sub if r["path_f1"] is not None]
        def avg(xs, fmt, scale=1):
            return "-" if not xs else fmt % (scale * sum(xs) / len(xs))

        print("%-8s %5d %8d/%d %6d/%d %8s %8s %6s %7.1f"
              % ("전역" if hop == 0 else "%d홉" % hop, len(sub),
                 sum(r["graph_ok"] for r in sub), len(sub),
                 sum(r["basic_ok"] for r in sub), len(sub),
                 avg(recs, "%.0f%%", 100), avg(pres, "%.0f%%", 100),
                 avg(f1s, "%.2f"),
                 sum(r["n_evidence"] for r in sub) / len(sub)))
    print("-" * 62)
    print("%-10s %6d %9d/%d %7d/%d"
          % ("합계", len(rows), sum(r["graph_ok"] for r in rows), len(rows),
             sum(r["basic_ok"] for r in rows), len(rows)))

    bad = [r for r in rows if not r["graph_ok"]]
    if bad:
        print("")
        print("실패 층 분류:")
        for layer in ("색인", "탐색", "생성"):
            ids = [r["id"] for r in bad if r["layer"] == layer]
            if ids:
                print("  %s(%d): %s" % (layer, len(ids), ", ".join(ids)))
    print("")
    print("→ output/eval.json")


if __name__ == "__main__":
    main()
