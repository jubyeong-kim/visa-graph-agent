"""지식 그래프 구축 — LLM 추출 + 규칙 보강 + 정규화 + 커뮤니티 탐지.

세 갈래로 삼중항을 모은다.
  ① LLM 추출   스키마로 노드/관계 타입을 묶어 문서에서 뽑는다 (느리고 돈이 든다 → 캐시)
  ② 규칙 보강   별표 1의2 에서 체류자격 정식 명칭을, 별표 1의3 에서 영주 전환을 뽑는다.
                규칙은 범위가 좁은 대신 틀리지 않는다. 이 도메인은 전환 관계 상당수가
                법령에 표 형태로 적혀 있어 LLM 을 부를 이유가 없다.
  ③ 정규화     표기 통일·별칭 병합·일반명사 제외. 여기서 길이 이어진다.

삼중항마다 origin(llm/rule)을 남긴다. 관계별 커버리지를 origin 별로 읽기 위해서다.
"""
import glob
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import networkx as nx

def load_env(path=".env"):
    """.env 를 읽어 환경변수로 올린다. python-dotenv 를 더 깔 이유가 없다."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()
CFG = json.load(open("config.json", encoding="utf-8"))
DOC_DIR, OUT, CACHE = "data/docs", "output", "output/cache"
VISA = re.compile(r"\(([A-H]-\d{1,2}(?:-\d)?)\)")
VISA_CODE = re.compile(r"[A-H]-\d{1,2}(?:-\d)?")
FORCE = "--force" in sys.argv


def load_docs():
    docs = {}
    for p in sorted(glob.glob(os.path.join(DOC_DIR, "*.md"))):
        raw = open(p, encoding="utf-8").read()
        title = raw.split("\n", 1)[0].lstrip("# ").strip()
        body = raw.split("\n\n", 2)[-1]
        docs[title] = body
    return docs


# ── ② 규칙 보강 ────────────────────────────────────────────────────────────
def rule_triples(docs):
    """법령 별표에서 규칙만으로 확실한 삼중항을 뽑는다."""
    out = []
    names = {}
    order = []          # 별표 1의2 에 적힌 순서. "A부터 B까지" 범위를 펼칠 때 쓴다.

    # 별표 1의2: "21. 비전문취업 (E-9) 「외국인근로자의…」" → 체류자격의 정식 명칭.
    # 이 명칭 사전이 정규화의 핵심이다. "비전문취업"과 "E-9"를 한 노드로 묶어 준다.
    ITEM = re.compile(r"\d+(?:의\d+)?\.\s*([가-힣·ㆍ\s]{2,12}?)\s*\(([A-H]-\d{1,2})\)")
    for title, body in docs.items():
        # 명칭은 체류자격 '표'에서만 뽑는다. 별표 1의3 은 줄글이라
        # "국민 또는 영주자격(F-5)를 가진 사람" 같은 문장이 명칭으로 잘못 딸려 온다.
        if "별표" not in title or ("체류자격" not in title):
            continue
        is_long = "장기체류자격" in title       # 범위의 기준이 되는 그 표
        for m in ITEM.finditer(body):
            label, code = m.group(1).strip(), m.group(2)
            if is_long and code not in order:
                order.append(code)
            if label and code not in names:
                names[code] = label
                out.append((code, "NAMED", label, "rule", title))

    names.setdefault("F-5", "영주")   # 영주는 별표 1의3 이 따로 정의해 표에 없다

    # 별표 1의3(영주자격): "…(D-7)부터 …(E-7)까지의 체류자격이나 …(F-2) 체류자격으로
    # 5년 이상 체류하고 있는 사람" → 그 자격들에서 F-5 로 가는 길이 있다는 뜻.
    #
    # "A부터 B까지"는 범위다. 양 끝만 잡으면 사이의 자격이 통째로 빠져 전환 경로가 끊긴다.
    # 별표 1의2 에 적힌 순서가 곧 범위의 순서이므로 그 순서로 펼친다.
    pos = {c: i for i, c in enumerate(order)}

    def codes_in(text):
        found = list(dict.fromkeys(VISA.findall(text)))
        for a, b in re.findall(r"\(([A-H]-\d{1,2})\)\s*부터.*?\(([A-H]-\d{1,2})\)\s*까지", text):
            if a in pos and b in pos and pos[a] <= pos[b]:
                found += order[pos[a]:pos[b] + 1]
        return [c for c in dict.fromkeys(found) if c != "F-5"]

    # 이 도메인에서 한국어 능력과 비자를 잇는 유일한 다리:
    #   "단계별 사회통합프로그램을 이수하면 영주 기본소양 요건 충족 인정"
    # LLM 에 맡겨 봤더니 실행할 때마다 나왔다 말았다 했다. 다리가 끊기면 한국어 관련
    # 질문이 통째로 답이 안 나온다. 문장이 안정적인 관공서 문구라 규칙으로 내린다.
    # 요건 이름은 지어내지 않고 본문에서 잡아 온다.
    BRIDGE = re.compile(r"사회통합\s*프로그램을?\s*이수하면\s*([가-힣\s]{2,20}?요건)\s*충족")
    for title, body in docs.items():
        for m in BRIDGE.finditer(body):
            req = re.sub(r"\s+", " ", m.group(1)).strip()
            out.append((req, "SATISFIED_BY", "사회통합프로그램", "rule", title))
            # "영주 기본소양 요건"이라는 이름 자체가 영주자격의 요건임을 말한다.
            # 이 엣지도 LLM 에 맡겼더니 실행마다 사라졌다. 이름이 근거이므로 규칙으로 내린다.
            if req.startswith("영주"):
                out.append(("F-5", "REQUIRES", req, "rule", title))

    # 하이코리아 안내문은 문서 제목이 곧 절차 이름이고("체류기간연장"),
    # 본문에 "관할 출입국관리사무소에 신청" 이라고 적혀 있다. 창구를 묻는 질문이
    # 가장 흔한데 LLM 은 이걸 실행마다 놓쳤다 — 실제로 체류기간연장은 노드조차
    # 안 생겼다. 제목과 문장이 정해져 있으니 규칙으로 내린다.
    OFFICE = re.compile(r"관할\s*출입국(?:관리사무소|·외국인관서|ㆍ외국인관서)")
    for title, body in docs.items():
        if "별표" in title or "제" in title[:12] and "조" in title[:12]:
            continue                      # 법령 조문은 제목이 절차명이 아니다
        if OFFICE.search(body) and ("신청" in body or "받아야" in body):
            out.append((title.split("/")[0].strip(), "HANDLED_BY",
                        "출입국·외국인청", "rule", title))

    for title, body in docs.items():
        if "영주자격" not in title or "별표" not in title:
            continue
        # 각 호로 쪼개되, 별표 항목 번호("20. 특정활동(E-7)")에서는 쪼개지 않는다.
        # 여기서 쪼개면 "10. 주재(D-7)부터 20. 특정활동(E-7)까지"가 반토막 나
        # 범위가 통째로 사라진다.
        for ho in re.split(r"(?=\s\d{1,2}\.\s(?![가-힣]{2,10}\s*\([A-H]-))", body):
            codes = codes_in(ho)
            if not codes:
                continue
            for c in codes:
                out.append((c, "CONVERTS_TO", "F-5", "rule", title))
            # 조건은 문장 끝까지 받아야 뜻이 산다. "…있는 사"처럼 잘리면 노드가 쓰레기가 된다.
            for req in re.findall(r"\d+년 이상[^,.]*?(?=하고 있|한 사람|인 사람|$)", ho):
                req = re.sub(r"\s+", " ", req).strip()
                if 4 <= len(req) <= 40:
                    for c in codes:
                        out.append((c, "REQUIRES", req, "rule", title))
    return out, names


# ── ① LLM 추출 ─────────────────────────────────────────────────────────────
DOMAIN_RULES = """
이 문서들은 대한민국 체류자격(비자) 제도의 공식 안내문과 법령이다. 다음을 지켜라.

- 체류자격은 반드시 코드로 적는다: "비전문취업"이 아니라 "E-9".
  본문에 그대로 적혀 있는 코드만 쓴다. 적혀 있지 않은 코드는 절대 지어내지 마라.

- CONVERTS_TO 는 **이미 가지고 있는 자격 A 를 버리고 B 로 바꾸는** 경우에만 쓴다.
  이런 것은 전환이다:
    "단기방문(C-3)으로 활동하고 있는 외국인이 투자(D-8)를 하려는 경우"
        → (C-3, CONVERTS_TO, D-8)
    "어학연수(D-4)를 마친 후 대학에 유학(D-2) 하고자 하는 경우"
        → (D-4, CONVERTS_TO, D-2)
    "E-7 자격으로 5년 이상 체류한 사람은 영주(F-5) 자격을 받을 수 있다"
        → (E-7, CONVERTS_TO, F-5) 그리고 (E-7, REQUIRES, 5년 이상 체류)

  아래 셋은 전환이 **아니다**. 제도가 다르다. 절대 CONVERTS_TO 로 만들지 마라.
    · 체류자격외활동 — 자격은 그대로 두고 다른 활동을 겸하는 허가.
      "D-2, D-6, E-1 자격자는 체류자격외활동허가를 받아야 한다"
        → (D-2, ALLOWS, 체류자격외활동허가) 처럼 각각 ALLOWS 로 뽑는다.
          앞뒤에 나열된 코드끼리 짝지어 (D-2, CONVERTS_TO, D-6) 을 만들지 마라.
    · 체류자격부여 — 자격이 없던 사람(국내 출생아, 국적 상실자)에게 새로 주는 것.
    · 근무처변경 — 자격은 그대로고 일하는 곳만 바뀌는 것.

- SATISFIED_BY 는 "그 요건은 이 프로그램을 이수하면 충족된다"일 때 쓴다. 놓치기 쉬우니 잘 봐라.
    "한국어능력에 따라 단계별 사회통합프로그램을 이수하면 영주 기본소양 요건 충족 인정"
        → (영주 기본소양 요건, SATISFIED_BY, 사회통합프로그램)
    "결혼이민(F-6) 사증신청 시 국제결혼 안내프로그램 이수증을 제출해야 함"
        → (F-6, REQUIRES, 국제결혼 안내프로그램 이수)
  한국어 능력·국어능력·기본소양은 Requirement 노드로 뽑는다. 이 도메인에서 가장 중요한 요건이다.

- 문서에 사실이 여러 개면 여러 개를 다 뽑아라. 한 문서에서 하나만 뽑고 끝내지 마라.
- REQUIRES 의 대상은 "5년 이상 체류", "사회통합프로그램 이수", "한국어능력시험 3급"처럼
  충족 여부를 판정할 수 있는 조건이어야 한다. "적법한 체류" 같은 막연한 말은 뽑지 마라.
- 수수료 금액과 처리 소요기간은 노드로 만들지 마라. 같은 금액이 수십 문서를 잇는 가짜 허브가 된다.
  다만 **신청 기한**은 다르다. 반드시 REQUIRES 로 뽑아라. 사용자가 가장 자주 묻는다.
    "체류기간이 만료하기 전 4개월부터 만료 당일까지 신청하여야 합니다"
        → (체류기간연장, REQUIRES, 만료 전 4개월부터 만료 당일까지)
    "사유가 발생한 날부터 90일 이내에 신청하여야 합니다"
        → (그 절차, REQUIRES, 사유 발생일부터 90일 이내)
- 국적별 예외는 뽑지 마라.
- 기관 이름은 문서에 적힌 그대로 쓴다.
"""


def extract_llm(docs):
    from langchain_core.documents import Document
    from langchain_experimental.graph_transformers import LLMGraphTransformer

    from llm import chat_model, model_id, require_key

    require_key()
    os.makedirs(CACHE, exist_ok=True)
    m = CFG["model"]
    llm = chat_model("chat")
    print("  모델:", model_id("chat"))
    tf = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=CFG["schema"]["nodes"],
        allowed_relationships=[tuple(r) for r in CFG["schema"]["relations"]],
        additional_instructions=DOMAIN_RULES,
    )

    def one(item):
        title, body = item
        cp = os.path.join(CACHE, re.sub(r'[\\/:*?"<>|]', "_", title) + ".json")
        if os.path.exists(cp) and not FORCE:
            return json.load(open(cp, encoding="utf-8"))
        text = "# " + title + "\n" + body
        gd = tf.convert_to_graph_documents(
            [Document(page_content=text, metadata={"source": title})])
        rels = [[r.source.id, r.type, r.target.id] for r in gd[0].relationships]
        # 주의: 삼중항이 0개로 나오는 문서가 있다(「국제 결혼안내프로그램」).
        # "빠짐없이 뽑아라"를 붙여 다시 물어봐도 여전히 0개였다. 그래서 재시도 코드는
        # 넣지 않았다. 효과가 없는 장치를 달아 두면 고쳐진 줄 알게 된다.
        # 이건 REPORT 의 색인층 실패로 남긴다.
        json.dump(rels, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        return rels

    items = list(docs.items())
    out = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=m["workers"]) as ex:
        for (title, _), rels in zip(items, ex.map(one, items)):
            out += [(h, r, t, "llm", title) for h, r, t in rels]
            print("  %3d개  %s" % (len(rels), title[:48]))
    print("LLM 추출 %.0f초 · 삼중항 %d개" % (time.time() - t0, len(out)))
    return out


def mark_grounding(triples, docs, names):
    """체류자격 코드가 출처 문서에 글자 그대로 있는지 표시한다. 버리지는 않는다.

    처음에는 없으면 버렸다. 그런데 그러다 가장 중요한 삼중항을 잘랐다 —
    「출입국관리법 제10조 영주자격」은 "영주자격"이라고만 쓰고 "F-5"를 안 쓴다.
    모델이 맞게 대응시킨 것인데 버려서 E-7 → F-5 → 기본소양 경로가 통째로 끊겼다.

    그래서 버리지 않고 grounded 로 표시만 한다. 코드가 아니라 한글 명칭으로 적힌
    경우까지 근거로 인정하되, 답변에서 원문을 인용할 때 어느 쪽인지 구별할 수 있게 한다.
    """
    marked = Counter()
    out = []
    for h, r, t, origin, src in triples:
        body = docs.get(src, "") + " " + src
        ok = True
        for x in (h, t):
            if not VISA_CODE.fullmatch(str(x)):
                continue
            label = names.get(x, "")
            if str(x) in body or (label and label in body):
                continue
            ok = False
        marked[ok] += 1
        out.append((h, r, t, origin if ok else origin + "-inferred", src))
    return out, marked


# ── ③ 정규화 ───────────────────────────────────────────────────────────────
DIGITS = re.compile(r"\d+")


def fuzzy_merge(triples, merged):
    """표기만 다른 같은 개체를 한 노드로 모은다.

    `출입국·외국인청` 과 `출입국ㆍ외국인청` 은 가운뎃점이 U+00B7 과 U+318D 로 다를 뿐
    같은 기관이다. 이런 게 갈려 있으면 멀티홉에서 길이 끊긴다.

    함정: 이 도메인은 숫자가 다르면 완전히 다른 뜻이다. "5년 이상 체류" 와
    "3년 이상 체류" 는 글자로는 92% 닮았지만 절대 합치면 안 된다. 그래서
    숫자 집합이 같을 때만 합친다.
    """
    from rapidfuzz import fuzz

    freq = Counter()
    from_rule = set()
    for h, _r, t, o, _s in triples:
        freq[h] += 1
        freq[t] += 1
        if o == "rule":
            from_rule.update((h, t))
    # 규칙이 만든 이름을 표준으로 삼는다. 규칙은 원문에서 그대로 떠 온 이름이고,
    # LLM 은 같은 것을 조금씩 잘라서 뱉는다. 이걸 안 하면 "영주 기본소양 요건"이
    # LLM 이 뱉은 "영주 기본소양"에 흡수되어 규칙이 만든 엣지가 길을 잃는다.
    # 그 다음은 자주 나온 것, 마지막이 짧은 것 순이다.
    nodes = sorted(freq, key=lambda n: (n not in from_rule, -freq[n], len(n)))
    canon = {}
    for n in nodes:
        hit = None
        for c in dict.fromkeys(canon.values()):
            if set(DIGITS.findall(n)) != set(DIGITS.findall(c)):
                continue
            if fuzz.ratio(n, c) >= 92 or (len(c) >= 6 and c in n):
                hit = c
                break
        canon[n] = hit or n
        if hit:
            merged[n] += 1

    # 자동으로 합치기엔 애매한 것들을 사람이 볼 수 있게 남긴다.
    # 기준을 낮춰 자동 병합하면 "3년 이상 체류"와 "5년 이상 체류"가 붙어 버린다.
    # 판단은 사람이 하고, 결정한 것만 config.json 의 aliases 로 내린다.
    review = []
    final = sorted(dict.fromkeys(canon.values()), key=len)
    for i, a in enumerate(final):
        for b in final[i + 1:]:
            if a == b or set(DIGITS.findall(a)) != set(DIGITS.findall(b)):
                continue
            if fuzz.token_set_ratio(a, b) >= 70 and len(a) >= 5:
                review.append((a, b, int(fuzz.token_set_ratio(a, b))))
    json.dump([{"a": a, "b": b, "score": s} for a, b, s in review],
              open(os.path.join(OUT, "merge_review.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if review:
        print("  사람이 볼 병합 후보 %d쌍 → output/merge_review.json" % len(review))

    return [(canon.get(h, h), r, canon.get(t, t), o, s) for h, r, t, o, s in triples]


def normalize(triples, names):
    al = CFG["normalize"]["aliases"]
    stop = set(CFG["normalize"]["stopwords"])
    code_of = {v: k for k, v in names.items()}          # 비전문취업 → E-9
    merged = Counter()

    def norm(x):
        x = str(x).replace("ㆍ", "·").replace("・", "·")   # 가운뎃점 세 종류를 하나로
        x = re.sub(r"\s+", " ", x).strip().strip("「」\"'")
        if x in al:
            merged[x] += 1
            return al[x]
        m = VISA.search(x) or VISA_CODE.fullmatch(x)
        if m:                                            # "비전문취업(E-9)" → "E-9"
            code = m.group(1) if m.lastindex else m.group(0)
            if x != code:
                merged[x] += 1
            return code
        if x in code_of:                                 # "비전문취업" → "E-9"
            merged[x] += 1
            return code_of[x]
        return x

    # 여기서 중복을 버리면 안 된다. 같은 사실을 말한 두 번째 문서의 출처가 사라진다.
    # 중복 정리는 출처를 모으는 agg 단계에서 한다.
    out = []
    for h, r, t, origin, src in triples:
        h, t = norm(h), norm(t)
        if not h or not t or h == t:
            continue
        if h in stop or t in stop or len(h) < 2 or len(t) < 2:
            continue
        out.append((h, r, t, origin, src))

    out = fuzzy_merge(out, merged)

    # 같은 사실을 여러 문서가 말하면 출처를 버리지 말고 모은다.
    # 버리면 "이 사실은 어느 문서에 있나"에 한 곳밖에 못 답한다.
    # 여러 문서가 같은 말을 한다는 것 자체가 근거의 강도이기도 하다.
    agg = {}
    for h, r, t, origin, src in out:
        if h == t:
            continue
        k = (h, r, t)
        if k not in agg:
            agg[k] = {"origins": [], "sources": []}
        if origin not in agg[k]["origins"]:
            agg[k]["origins"].append(origin)
        if src not in agg[k]["sources"]:
            agg[k]["sources"].append(src)
    # 규칙으로도 나온 사실은 규칙을 origin 으로 삼는다 (더 믿을 만하다)
    return [(h, r, t,
             "rule" if "rule" in v["origins"] else v["origins"][0],
             v["sources"])
            for (h, r, t), v in agg.items()], merged


def build(triples, names):
    # MultiDiGraph 를 쓴다. DiGraph 는 한 노드쌍에 엣지를 하나만 담아서,
    # 같은 사실을 여러 문서가 말하면 출처가 덮어씌워진다. 실제로 그랬다 —
    # (외국인등록 → 출입국·외국인청) 의 출처가 엉뚱한 조문으로 찍혔다.
    # 근거와 출처를 보여 주는 게 이 과제의 핵심이라 출처를 다 들고 있어야 한다.
    G = nx.MultiDiGraph()
    for h, r, t, origin, src in triples:
        if r == "NAMED":
            G.add_node(h, type="Visa", label=t)
            continue
        for n in (h, t):
            if n not in G:
                G.add_node(n, type="Visa" if VISA_CODE.fullmatch(n) else "", label="")
        G.add_edge(h, t, relation=r, origin=origin,
                   source=src[0] if isinstance(src, list) else src,
                   sources="; ".join(src) if isinstance(src, list) else src,
                   n_sources=len(src) if isinstance(src, list) else 1)
    for code, label in names.items():
        if code in G:
            G.nodes[code]["label"] = label
            G.nodes[code]["type"] = "Visa"
    return G


def communities(G):
    U = G.to_undirected()
    parts = nx.community.louvain_communities(U, seed=42)
    parts = [sorted(p, key=lambda n: -U.degree(n)) for p in parts if len(p) >= 3]
    return sorted(parts, key=len, reverse=True)


COMMUNITY_PROMPT = """다음은 대한민국 체류자격 지식 그래프에서 찾아낸 한 무리다.
이 무리가 어떤 제도 갈래인지 한국어로 요약하라.

형식:
제목: (6자 이내)
요약: (2~3문장. 이 무리가 무엇에 관한 것인지, 어떤 질문에 답할 수 있는지)

무리에 속한 개체와 관계:
{facts}
"""


def summarize_communities(G, parts):
    """무리마다 요약을 미리 만들어 둔다.

    전역 질문("이 자료에 어떤 갈래가 있나")은 상위 k개 문서로는 답할 수 없다.
    질문이 들어온 뒤에 만들면 느리고 비싸니 색인 시점에 만들어 캐시한다.
    """
    from llm import chat_model

    llm = chat_model("chat")
    out = []
    for i, members in enumerate(parts):
        S = set(members)
        facts = ["(%s) -[%s]-> (%s)" % (u, d["relation"], v)
                 for u, v, d in G.edges(data=True) if u in S and v in S][:60]
        if not facts:
            facts = list(members)[:30]
        txt = llm.invoke(COMMUNITY_PROMPT.format(facts="\n".join(facts))).content.strip()
        title = re.search(r"제목:\s*(.+)", txt)
        out.append({"id": i, "size": len(members),
                    "title": title.group(1).strip() if title else "무리 %d" % i,
                    "summary": txt, "members": members[:40]})
        print("  #%d (%d개) %s" % (i, len(members), out[-1]["title"]))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    docs = load_docs()
    print("문서 %d건" % len(docs))

    print("")
    print("규칙 보강…")
    rules, names = rule_triples(docs)
    print("  체류자격 명칭 %d개 · 규칙 삼중항 %d개" % (len(names), len(rules)))

    print("")
    print("LLM 추출…")
    llm = extract_llm(docs)

    print("")
    print("근거 표시…")
    llm, marked = mark_grounding(llm, docs, names)
    print("  코드가 원문에 그대로 있음 %d개 · 명칭으로만 있음(inferred) %d개"
          % (marked[True], marked[False]))

    print("")
    print("정규화…")
    tri, merged = normalize(rules + llm, names)
    print("  삼중항 %d → %d (중복·불용어 제거)" % (len(rules) + len(llm), len(tri)))
    print("  표기 병합 상위: %s" % merged.most_common(8))

    G = build(tri, names)
    cm = communities(G)
    print("")
    print("노드 %d · 엣지 %d · 커뮤니티 %d개"
          % (G.number_of_nodes(), G.number_of_edges(), len(cm)))
    print("  관계별:", Counter(d["relation"] for _, _, d in G.edges(data=True)).most_common())
    print("  origin:", Counter(d["origin"] for _, _, d in G.edges(data=True)).most_common())

    nx.write_graphml(G, os.path.join(OUT, "graph.graphml"))
    cpath = os.path.join(OUT, "communities.json")
    if FORCE or not os.path.exists(cpath):
        print("")
        print("커뮤니티 요약…")
        summaries = summarize_communities(G, cm)
        json.dump(summaries, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    else:
        print("  커뮤니티 요약은 캐시 사용 (--force 로 다시 만듦)")
    json.dump([{"h": h, "r": r, "t": t, "origin": o, "sources": s} for h, r, t, o, s in tri],
              open(os.path.join(OUT, "triples.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    n_conv = sum(1 for _, _, d in G.edges(data=True) if d["relation"] == "CONVERTS_TO")
    print("")
    print("CONVERTS_TO %d개 — %s"
          % (n_conv, "OK" if n_conv >= 15 else "부족: 추출 프롬프트 손볼 것"))


if __name__ == "__main__":
    main()
