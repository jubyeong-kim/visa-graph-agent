"""규칙 보강과 정규화 자체 검사. `python test_rules.py`

여기만 검사하는 이유: 범위 펼치기("A부터 B까지")와 호 쪼개기는 조용히 깨진다.
깨져도 그래프는 멀쩡해 보이고, 전환 관계만 소리 없이 사라진다.
실제로 한 번 깨졌다 — 호 단위로 쪼갤 때 별표 항목 번호("20.")에서 잘려
D-7~E-7 범위가 통째로 날아갔다.
"""
import build_graph as B

docs = B.load_docs()
rules, names = B.rule_triples(docs)
tri, merged = B.normalize(rules, names)
G = B.build(tri, names)

conv = {h for h, t, d in G.edges(data=True) if d["relation"] == "CONVERTS_TO" and t == "F-5"}
req = [t for _, t, d in G.edges(data=True) if d["relation"] == "REQUIRES"]

# 별표 1의2 의 체류자격 명칭이 다 잡혀야 정규화 사전이 제 일을 한다
assert len(names) >= 30, f"체류자격 명칭 {len(names)}개 — 별표 파싱이 깨졌다"
assert names.get("E-9") == "비전문취업", f"E-9 명칭이 {names.get('E-9')!r}"

# "주재(D-7)부터 특정활동(E-7)까지" — 양 끝뿐 아니라 사이도 펼쳐져야 한다
for code in ("D-7", "D-8", "D-9", "E-1", "E-6", "E-7"):
    assert code in conv, f"{code} → F-5 가 없다. 범위 펼치기가 깨졌다"
assert "F-2" in conv and "F-4" in conv, "범위 밖에 따로 적힌 자격이 빠졌다"
assert "F-5" not in conv, "F-5 → F-5 자기 자신으로 가는 엣지가 생겼다"

# 체류기간 조건은 문장 끝까지 와야 뜻이 산다. "…있는 사" 처럼 잘리면 안 된다.
# (기본소양 요건처럼 기간이 아닌 REQUIRES 도 있으므로 기간 조건만 검사한다)
assert req, "REQUIRES 가 하나도 없다"
years = [r for r in req if "년" in r]
assert years, "체류기간 조건이 하나도 없다"
for r in years:
    assert not r.endswith(("있는 사", "하고 있", "인 사")), f"조건이 잘렸다: {r!r}"
    assert "년 이상" in r, f"기간 조건 같지 않다: {r!r}"

# 정규화: 표기가 다른 같은 것이 한 노드로 모여야 한다
assert B.normalize([("비전문취업(E-9)", "CONVERTS_TO", "영주(F-5)", "t", "x")], names)[0][0][:3] \
    == ("E-9", "CONVERTS_TO", "F-5"), "괄호 안 코드 정규화가 깨졌다"
assert B.normalize([("비전문취업", "CONVERTS_TO", "F-5", "t", "x")], names)[0][0][0] == "E-9", \
    "한글 명칭 → 코드 정규화가 깨졌다"

# 한국어 능력과 비자를 잇는 유일한 다리. LLM 에 맡겼더니 실행마다 나왔다 말았다 했다.
# 이게 끊기면 "영주권 받으려면 한국어 얼마나?" 류 질문이 통째로 답이 안 나온다.
bridge = [(h, t) for h, t, d in G.edges(data=True) if d["relation"] == "SATISFIED_BY"]
assert ("영주 기본소양 요건", "사회통합프로그램") in bridge, \
    f"한국어 요건 다리가 끊겼다. 현재 SATISFIED_BY: {bridge}"

print(f"통과 — 명칭 {len(names)}개 · CONVERTS_TO {len(conv)}개 · REQUIRES {len(req)}개 "
      f"· 한국어 다리 OK")
