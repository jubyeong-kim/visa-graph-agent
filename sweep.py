"""근거 상한을 쓸어 보며 재현율이 어디서 무너지는지 찾는다. `python sweep.py`

**LLM 을 부르지 않는다.** 근거 수집은 순수 그래프 연산이라 재현율·정밀도·F1 을
공짜로 잴 수 있다. 채점(정답률)만 모델이 필요하고, 그건 곡선에서 지점을 정한 뒤에
`evaluate.py` 로 한 번만 돌리면 된다.

한 번의 설정으로 "좋아졌다"고 말하는 것과, 곡선을 보고 "여기가 꺾이는 지점"이라고
말하는 것은 다르다. 앞엣것은 운일 수 있다.
"""
import json
import sys

import agent
from evaluate import path_scores

GOLD = [i for i in json.load(open("data/goldenset.json", encoding="utf-8"))["items"]
        if i["expected_path"]]
CAPS = [int(x) for x in sys.argv[1:]] or [4, 6, 8, 10, 12, 14, 18, 24, 30, 999]


def run(cap):
    agent.TRV["evidence_cap"] = cap
    rows = []
    for it in GOLD:
        seeds = agent.find_entities(it["q"])
        out = agent.expand({"question": it["q"], "seeds": seeds, "deepened": 1})
        ev = out["evidence"]
        rec, pre, f1, _ = path_scores(it["expected_path"], ev)
        rows.append((it["hops"], rec, pre, f1, len(ev)))
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


print("근거 상한을 바꿔 가며 잰다 (LLM 호출 없음, 문항 %d개)" % len(GOLD))
print("")
print("%6s %8s %8s %6s %8s   %s" % ("상한", "재현", "정밀", "F1", "근거수", "홉별 재현(1/2/3)"))
best = None
for cap in CAPS:
    rows = run(cap)
    rec, pre, f1 = mean([r[1] for r in rows]), mean([r[2] for r in rows]), mean([r[3] for r in rows])
    n = mean([r[4] for r in rows])
    byhop = "/".join("%.0f%%" % (100 * mean([r[1] for r in rows if r[0] == h])) for h in (1, 2, 3))
    print("%6s %7.0f%% %7.0f%% %6.2f %8.1f   %s"
          % ("없음" if cap >= 999 else cap, 100 * rec, 100 * pre, f1, n, byhop))
    if best is None or f1 > best[1]:
        best = (cap, f1, rec)

print("")
print("F1 최대: 상한 %s (F1 %.2f, 재현 %.0f%%)"
      % ("없음" if best[0] >= 999 else best[0], best[1], 100 * best[2]))
print("F1 이 가장 높은 지점이 곧 최선은 아니다. 재현율이 꺾이기 직전을 고르는 것이")
print("이 도메인에서는 맞다 — 근거를 못 데려오면 답 자체가 안 나온다.")
