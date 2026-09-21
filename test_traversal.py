"""시작 개체 인식과 근거 선별 자체 검사. `python test_traversal.py`

이 둘은 LLM 을 부르지 않는다 — 순수 그래프 연산이라 키 없이 검사할 수 있다.
그리고 둘 다 조용히 깨진다. 깨져도 에이전트는 답을 내놓고, 그 답이 조금 틀릴 뿐이다.

실제로 겪은 두 가지를 지킨다.
  · "외국인등록 사항이 바뀌면"이 노드 `외국인등록사항변경 신고의무` 를 못 집었다.
    부분일치 점수로는 더 짧고 정확한 `외국인등록`(100점)에 밀린다.
  · 차수 20짜리 노드 하나에 닿으면 근거 20개가 통째로 들어왔다. 3홉 질문에 34개를
    퍼와서 3개만 썼다. 생성 모델이 잡음에 묻혀 답을 놓쳤다.
"""
import agent

# ── 시작 개체 ──────────────────────────────────────────────────────────────
s = agent.find_entities("E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?")
assert "E-7" in s, f"코드를 못 집었다: {s}"

s = agent.find_entities("외국인등록 사항이 바뀌면 신고할 때 무엇을 가져가야 하나요?")
assert any("변경" in x for x in s), f"긴 절차명을 못 집었다: {s}"

s = agent.find_entities("아무 관계 없는 질문입니다 날씨가 좋네요")
assert len(s) <= 2, f"관계없는 질문에 시작 개체가 너무 많다: {s}"

# ── 근거 선별 ──────────────────────────────────────────────────────────────
CAP = agent.TRV["evidence_cap"]
for q, seeds in [("E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?", ["E-7", "F-5"]),
                 ("외국인등록은 어디에 신청하나요?", ["외국인등록"])]:
    out = agent.expand({"question": q, "seeds": seeds, "deepened": 1})
    ev = out["evidence"]
    assert ev, f"근거가 하나도 없다: {q}"
    assert len(ev) <= CAP, f"근거 상한 {CAP} 을 넘었다: {len(ev)}개"
    # 한 노드가 근거를 독점하지 않는다
    from collections import Counter
    top = Counter(h for h, _r, _t, _s, _hop in ev).most_common(1)[0]
    assert top[1] <= agent.TRV["per_node_cap"], f"한 노드가 {top[1]}개를 냈다: {top[0]}"

# 간판 3홉 경로는 선별 뒤에도 살아 있어야 한다.
# 근거를 줄이다가 이걸 끊으면 한국어 관련 질문이 통째로 죽는다.
out = agent.expand({"question": "E-7 비자인데 영주권을 받으려면 한국어는 어떻게 준비하나요?",
                    "seeds": ["E-7", "F-5"], "deepened": 1})
edges = {(h, r, t) for h, r, t, _s, _hop in out["evidence"]}
for e in [("E-7", "CONVERTS_TO", "F-5"),
          ("F-5", "REQUIRES", "영주 기본소양 요건"),
          ("영주 기본소양 요건", "SATISFIED_BY", "사회통합프로그램")]:
    assert e in edges, f"3홉 경로가 선별에서 잘렸다: {e}"

# 영주(F-5)는 차수가 20이라 허브 기준에 걸리지만, 통과시켜야 한다.
# 여기서 막으면 이 도메인에서 가장 중요한 길이 끊긴다.
assert not agent.is_hub("F-5", []), "체류자격을 허브로 막으면 영주 경로가 끊긴다"

print(f"통과 — 시작 개체 인식 · 근거 상한 {CAP} · 3홉 경로 보존 · F-5 통과")


# ── 숫자 검증기의 탐지부 ────────────────────────────────────────────────────
# 검증기 자체가 일하는지는 아무도 재지 않는다.
# 실제로 골든셋 12문항에서 한 번도 안 걸렸는데, 안 걸린 건지 고장 난 건지 몰랐다.
# 찔러 보니 탐지는 맞는데 재작성이 문장을 망가뜨리고 있었다.
# 탐지부는 LLM 이 필요 없으므로 여기서 지킨다 (재작성은 키가 있어야 검사할 수 있다).
ctx = "(F-5) -[REQUIRES]-> (5년 이상 체류)"
hit = [n for n in agent.NUM.findall("3년 이상 체류하고 TOPIK 4급이 필요합니다")
       if n.replace(" ", "") not in ctx.replace(" ", "")]
assert "3년" in hit and "4급" in hit, f"근거에 없는 숫자를 못 잡는다: {hit}"

miss = [n for n in agent.NUM.findall("5년 이상 체류해야 합니다")
        if n.replace(" ", "") not in ctx.replace(" ", "")]
assert not miss, f"근거에 있는 숫자를 잘못 잡는다(거짓 경보): {miss}"

# ── 사람 말 ↔ 문서 말 ───────────────────────────────────────────────────────
# 골든셋을 내가 다 써서
# `E-7` 같은 코드로만 물었고, "알바"·"결혼비자" 로는 아무것도 못 집었다.
assert agent.find_entities("알바 하려면 어떻게 하나요"), "'알바'로 시작 개체를 못 집는다"
assert "F-6" in agent.find_entities("결혼비자 받으려면 뭐가 필요해요"), "'결혼비자'가 F-6 에 안 닿는다"

print("통과 — 숫자 검증 탐지 · 사람 말 ↔ 문서 말")
