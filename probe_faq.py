"""외부 질문으로 찔러 본다. `python probe_faq.py`

채점기가 아니다. **내가 쓰지 않은 질문**에 에이전트가 어떻게 반응하는지 사람이 읽어
보는 도구다. 골든셋 12문항은 전부 내가 써서 `E-7` 같은 코드로만 물었고, 그래서
"알바"·"결혼비자" 로는 아무것도 못 집는 구멍이 안 보였다.

앞 프로젝트에서 배운 것: 평가셋 100% 가 말해 주지 않는 것이 있고, 그걸 찾아낸 채널은
언제나 **내가 만들지 않은 질문**이었다.
"""
import json
import sys

import agent

P = json.load(open("data/probe_faq.json", encoding="utf-8"))
print(P["source"])
print("")

rows = []
for i, q in enumerate(P["questions"], 1):
    r = agent.ask(q)
    dunno = "확인되지 않" in r["answer"] or "자료에 없" in r["answer"]
    print("─" * 70)
    print("[%02d] %s" % (i, q))
    print("     경로 %s · 시작 개체 %s · 근거 %d개%s"
          % (r["route"], r["seeds"] or "없음", len(r["evidence"]),
             "  ← 모른다고 답함" if dunno else ""))
    print("     %s" % r["answer"].replace("\n", " ")[:220])
    rows.append({"q": q, "route": r["route"], "seeds": r["seeds"],
                 "n_evidence": len(r["evidence"]), "refused": dunno,
                 "answer": r["answer"], "path": r["path"], "sources": r["sources"]})

json.dump(rows, open("output/probe_faq.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print("")
print("=" * 70)
n = len(rows)
print("질문 %d건 · 시작 개체 못 찾음 %d건 · 모른다고 답함 %d건"
      % (n, sum(1 for r in rows if not r["seeds"]), sum(1 for r in rows if r["refused"])))
print("경로 분포:", {k: sum(1 for r in rows if r["route"] == k)
                  for k in ("local", "path", "global")})
print("→ output/probe_faq.json (사람이 읽는다)")
