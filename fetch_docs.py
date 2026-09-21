"""코퍼스 수집 — 하이코리아 안내 + 출입국관리법 시행령 별표.

두 소스를 쓴다.
  1) 하이코리아 /info/InfoDatail.pt  — 절차·국적·동포·난민 안내. 줄글, 띄어쓰기 정상.
  2) 출입국관리법 시행령 별표(HWP)   — 체류자격 정의와 영주자격 요건.
     법령은 저작권법 제7조상 보호 대상이 아니라 자유롭게 쓸 수 있다.
     같은 별표의 PDF 는 공백 문자가 없어 쓸 수 없다. HWP 만 띄어쓰기가 살아 있다.

저장 형식은 수업 코퍼스와 같게 맞춘다 (`분류:` 줄을 나중에 규칙 기반 보강에 쓴다).
"""
import io, json, os, re, struct, sys, time, zlib
import olefile, requests

OUT = "data/docs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
LAW_XML = ("https://www.law.go.kr/DRF/lawService.do?OC=test&target=law&type=XML"
           "&LM=%EC%B6%9C%EC%9E%85%EA%B5%AD%EA%B4%80%EB%A6%AC%EB%B2%95%20%EC%8B%9C%ED%96%89%EB%A0%B9")

# 위키 토막글용 800자 대신 400자를 쓴다. 정부 안내문은 군더더기가 없어 같은 정보를
# 절반 길이로 담는다. 800자로 자르면 "C-3→D-8 변경" 같은 알짜 문서가 버려진다.
MIN_CHARS = 400

VISA = re.compile(r"\(([A-H]-\d{1,2}(?:-\d)?)\)")
LAWNM = re.compile(r"「([^」]{4,40})」")          # 「...」 로 인용된 법률명

# 도메인 지식이 아닌 안내 페이지는 버린다. 그래프에 넣어봐야 개체가 안 엮힌다.
DROP = {"국적증서수여식", "동포관련 사이트", "지원부서 안내",
        "개인정보 수집·이용안내", "배정 현황 및 참여 광역 지방정부 연락처"}

# 조문을 문서로 쓸 법률과, 제목에서 찾을 도메인 키워드
LAWS = ["출입국관리법", "출입국관리법 시행령", "국적법",
        "재한외국인 처우 기본법", "외국인근로자의 고용 등에 관한 법률"]
CORE = re.compile(r"체류자격|영주자격|사증|외국인등록|귀화|체류기간|근무처|고용허가")
ART_KEY = re.compile(r"체류|사증|입국|외국인등록|자격|영주|귀화|국적|고용|허가|신고|근무처|활동|연장|변경|부여|초청|등록증")


def clean(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = (t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
          .replace("&nbsp;", " ").replace("&quot;", '"'))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def fetch_hikorea(S, cat, par):
    url = f"https://www.hikorea.go.kr/info/InfoDatail.pt?CAT_SEQ={cat}&PARENT_ID={par}"
    h = S.get(url, timeout=25).text
    i = h.find("printContents")
    if i < 0:
        return None, url
    i = h.find(">", i) + 1          # 컨테이너 여는 태그를 건너뛴다
    t = clean(h[i:])
    # 본문은 '작성일' 또는 '인쇄하기' 앞까지다. 그 뒤는 전부 푸터.
    for mark in ("< 작성일", "인쇄하기"):
        j = t.find(mark)
        if j > 0:
            t = t[:j]
            break
    return t.strip(), url


def hwp_text(data):
    """HWP 5.0 본문 추출. 문단 텍스트 레코드(tag id 67)만 모은다."""
    f = olefile.OleFileIO(io.BytesIO(data))
    compressed = bool(f.openstream("FileHeader").read()[36] & 1)
    out = []
    for s in ("/".join(p) for p in f.listdir()):
        if not s.startswith("BodyText"):
            continue
        d = f.openstream(s).read()
        if compressed:
            d = zlib.decompress(d, -15)
        i = 0
        while i < len(d) - 4:
            hdr = struct.unpack_from("<I", d, i)[0]
            tid, ln = hdr & 0x3FF, (hdr >> 20) & 0xFFF
            i += 4
            if tid == 67:
                out.append(d[i:i + ln].decode("utf-16le", "ignore"))
            i += ln
    t = re.sub(r"[\x00-\x1f]", " ", "".join(out))
    # 제어 레코드에서 새는 한자 쓰레기를 턴다 (예: 汤捯)
    t = re.sub(r"[^가-힣 -~·‘-”・「」ㆍ]", " ", t)
    return re.sub(r" {2,}", " ", t).strip()


def fetch_law_byl(S):
    """시행령 별표 중 체류자격 관련 세 건을 HWP 로 받아 텍스트로 만든다."""
    x = S.get(LAW_XML, timeout=60).text
    want = {"000100E": "단기체류자격", "000102E": "장기체류자격", "000103E": "영주자격"}
    docs = []
    for key, short in want.items():
        m = re.search(r'<별표단위 별표키="%s">(.*?)</별표단위>' % key, x, re.S)
        if not m:
            continue
        blk = m.group(1)
        title = re.search(r"<별표제목><!\[CDATA\[(.*?)\]\]>", blk).group(1)
        link = re.search(r"<별표서식파일링크>(.*?)</별표서식파일링크>", blk).group(1)
        url = "https://www.law.go.kr" + link
        body = hwp_text(S.get(url, timeout=60).content)
        docs.append((f"출입국관리법 시행령 별표 {title}", short, body, url))
        time.sleep(0.5)
    return docs


def fetch_law_articles(S):
    """법령 조문을 문서 단위로 받는다.

    별표와 달리 조문은 XML 안에 텍스트가 그대로 있고 띄어쓰기도 멀정하다.
    제목이 도메인 키워드에 걸리는 조문만 골라 무관한 조문(벌칙·서식 등)을 제외한다.
    """
    for nm in LAWS:
        x = S.get("https://www.law.go.kr/DRF/lawService.do",
                  params={"OC": "test", "target": "law", "type": "XML", "LM": nm},
                  timeout=60).text
        for a in re.findall(r"<조문단위.*?</조문단위>", x, re.S):
            ti = re.search(r"<조문제목><!\[CDATA\[(.*?)\]\]>", a)
            if not ti or not ART_KEY.search(ti.group(1)):
                continue
            body = " ".join(re.findall(r"<(?:조문내용|항내용|호내용|목내용)><!\[CDATA\[(.*?)\]\]>", a, re.S))
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            no = re.search(r"<조문번호>(\d+)</조문번호>", a)
            yield f"{nm} 제{no.group(1) if no else '?'}조 {ti.group(1)}", body,                   f"https://www.law.go.kr/법령/{nm}"
        time.sleep(0.5)


def save(title, cats, body, url, manifest, dtype):
    name = re.sub(r'[\/:*?"<>|]', "_", title).replace(" ", "_")
    path = os.path.join(OUT, name + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n분류: {', '.join(cats)}\n\n{body}\n")
    manifest.append({"title": title, "type": dtype, "chars": len(body),
                     "source": url, "categories": cats})


def main():
    os.makedirs(OUT, exist_ok=True)
    S = requests.Session()
    S.headers.update({"User-Agent": UA, "Referer": "https://www.law.go.kr/"})
    manifest, skipped = [], []

    pages = json.load(open("data/_hikorea_pages.json", encoding="utf-8"))
    print(f"하이코리아 {len(pages)}건 수집…")
    for p in pages:
        if p["name"] in DROP:
            skipped.append((p["name"], "도메인 지식 아님 — 안내/연락처 페이지"))
            continue
        try:
            body, url = fetch_hikorea(S, p["cat"], p["par"])
        except Exception as e:
            skipped.append((p["name"], f"수집 실패: {type(e).__name__}"))
            continue
        if not body or len(body) < MIN_CHARS:
            skipped.append((p["name"], f"본문 {len(body or '')}자 < {MIN_CHARS}"))
            continue
        cats = ["안내"] + sorted(set(VISA.findall(body)))
        save(p["name"], cats, body, url, manifest, "Guide")
        print(f"  {len(body):>5}자  {p['name']}")
        time.sleep(0.3)

    print("")
    print("법령 조문 수집")
    for title, body, url in fetch_law_articles(S):
        # 길이만으로는 벌칙·서식 조문이 딸려 온다. 본문에 도메인 앵커가 있어야 남긴다.
        # 앵커 없는 조문은 그래프에서 다른 문서와 이어지지 않아 겹침률만 희석한다.
        if len(body) < MIN_CHARS or not CORE.search(body):
            continue
        cats = ["법령", "조문"] + sorted(set(VISA.findall(body)))
        save(title, cats, body, url, manifest, "Law")
        print(f"  {len(body):>5}자  {title}")

    print("\n법령 별표 수집…")
    for title, short, body, url in fetch_law_byl(S):
        if len(body) < MIN_CHARS:
            skipped.append((title, f"본문 {len(body)}자"))
            continue
        cats = ["법령", short] + sorted(set(VISA.findall(body)))
        save(title, cats, body, url, manifest, "Law")
        print(f"  {len(body):>5}자  {title}")

    json.dump({"docs": manifest, "skipped": skipped, "min_chars": MIN_CHARS},
              open("data/manifest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ── 판정: 건수와 개체 겹침 ──────────────────────────────────────────
    # 개체를 문서 제목에서 뽑는다. 위키 코퍼스에서 문서 제목이 곧 개체인 것과 같은 원리다.
    # 손으로 고른 단어 목록을 쓰면 지표를 원하는 쪽으로 맞추는 셈이라 쓰지 않는다.
    # 법령명 접두어("출입국관리법 시행령")는 모든 법령 문서에 붙어 있어 반드시 걷어낸다.
    def keyterms(title):
        t = title
        for L in LAWS:
            t = t.replace(L, "")
        t = re.sub(r"^\s*제\d+조(의\d+)?\s*|별표\s*", "", t)
        t = re.sub(r"\(.*?\)", "", t).strip()
        return [w for w in re.split(r"[/·,ㆍ]|\s+", t) if len(w) >= 3]

    bodies = {}
    for d in manifest:
        fn = re.sub(r'[\/:*?"<>|]', "_", d["title"]).replace(" ", "_") + ".md"
        bodies[d["title"]] = open(os.path.join(OUT, fn), encoding="utf-8").read()
    KEY = {t: set(keyterms(t)) for t in bodies}

    shared = 0
    for t, b in bodies.items():
        hit = any(o != t and any(k in b for k in ks) for o, ks in KEY.items())
        if not hit:                       # 제목이 안 걸리면 비자 코드로 한 번 더 본다
            v = set(VISA.findall(b))
            hit = any(v & set(VISA.findall(bodies[o])) for o in bodies if o != t)
        shared += hit

    # 멀티홉의 척추. 한 문서 안에 체류자격이 둘 이상 나와야 전환 관계가 뽑힐 수 있다.
    multi = sum(1 for b in bodies.values() if len(set(VISA.findall(b))) >= 2)

    n = len(manifest)
    print("")
    print("=" * 56)
    print(f"문서 {n}건 · 제외 {len(skipped)}건")
    print(f"길이 중앙값 {sorted(d['chars'] for d in manifest)[n // 2]:,}자")
    print(f"개체 겹침 {shared}/{n} = {shared / n:.0%}   (기준 60%)")
    print(f"체류자격 2개 이상 담은 문서 {multi}건  ← 전환 관계 후보")
    ok = n >= 50 and shared / n >= 0.6
    print(f"판정: {'통과 — 그래프 구축으로 진행' if ok else '미달 — 소스 보강 필요'}")


if __name__ == "__main__":
    main()
