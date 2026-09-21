"""보는 것들을 만든다 — 스키마 구조도(Mermaid) + 동적 그래프(HTML).

`python make_views.py`

둘 다 **`config.json` 과 `output/graph.graphml` 에서 뽑는다.** 손으로 그리면
스키마를 고쳤을 때 그림만 옛날 것으로 남는다. 문서가 코드를 따라오게 한다.
"""
import json
import os
import re
from collections import Counter

import networkx as nx

CFG = json.load(open("config.json", encoding="utf-8"))
G = nx.read_graphml("output/graph.graphml")
OUT = "output"
VISA_CODE = re.compile(r"[A-H]-\d{1,2}(?:-\d)?")

# 타입별 색. 명도 차이를 충분히 둬서 흑백으로 인쇄해도 구별되게 한다.
COLOR = {
    "Visa":         ("#1f4e79", "체류자격"),
    "Procedure":    ("#2e7d32", "절차"),
    "Requirement":  ("#b45309", "요건"),
    "Organization": ("#6b21a8", "기관"),
    "Program":      ("#0e7490", "제도"),
    "Document":     ("#64748b", "서류"),
}
SPINE = {"CONVERTS_TO", "REQUIRES", "SATISFIED_BY"}


def node_type(n):
    """그래프에 type 이 비어 있는 노드가 많다. 관계로 되짚어 추정한다."""
    t = G.nodes[n].get("type") or ""
    if t in COLOR:
        return t
    if VISA_CODE.fullmatch(n):
        return "Visa"
    rels_in = {d["relation"] for _u, _v, d in G.in_edges(n, data=True)}
    rels_out = {d["relation"] for _u, _v, d in G.out_edges(n, data=True)}
    if "HANDLED_BY" in rels_in or "OPERATED_BY" in rels_in:
        return "Organization"
    if "SUBMITS" in rels_in:
        return "Document"
    if "SATISFIED_BY" in rels_in or "REQUIRES" in rels_in:
        return "Requirement"
    if "SATISFIED_BY" in rels_out or "OPERATED_BY" in rels_out:
        return "Program"
    if "HANDLED_BY" in rels_out or "SUBMITS" in rels_out:
        return "Procedure"
    return "Document"


# ── ① 스키마 구조도 ────────────────────────────────────────────────────────
def schema_mermaid():
    rels = [tuple(r) for r in CFG["schema"]["relations"]]
    counted = Counter(d["relation"] for _u, _v, d in G.edges(data=True))
    lines = ["```mermaid", "graph LR"]
    for t, (c, kr) in COLOR.items():
        lines.append(f'  {t}["{t}<br/>{kr}"]')
    lines.append("")
    for src, rel, dst in rels:
        n = counted.get(rel, 0)
        style = "==>" if rel in SPINE else "-->"      # 척추는 굵게
        lines.append(f'  {src} {style}|"{rel} ({n})"| {dst}')
    lines.append("")
    for t, (c, _kr) in COLOR.items():
        lines.append(f"  style {t} fill:{c},color:#fff,stroke:none")
    lines.append("```")
    return "\n".join(lines)


# ── ② 동적 그래프 ──────────────────────────────────────────────────────────
HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>체류자격 지식 그래프</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/dist/vis-network.min.js"></script>
<style>
  :root {
    --bg:#ffffff; --fg:#1c2431; --muted:#4b5563; --line:#e5e7eb; --panel:#f4f6f9;
  }
  :root:not([data-theme="light"]) { }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#0f1319; --fg:#e8eaed; --muted:#9aa3af; --line:#2a313b; --panel:#171c24; } }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--fg); font-size:15px;
         font-family:system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif; }
  header { padding:14px 16px; border-bottom:1px solid var(--line) }
  h1 { margin:0 0 4px; font-size:17px; font-weight:650 }
  .sub { color:var(--muted); font-size:13px; line-height:1.5 }
  .bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center;
         padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel) }
  input, select { font:inherit; padding:6px 9px; border:1px solid var(--line);
                  border-radius:7px; background:var(--bg); color:var(--fg) }
  input { min-width:min(240px,60vw) }
  .legend { display:flex; gap:12px; flex-wrap:wrap; margin-left:auto; font-size:12.5px }
  .legend span { display:inline-flex; align-items:center; gap:5px; color:var(--muted) }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block }
  #net { height:calc(100vh - 165px); min-height:380px }
  #info { position:absolute; right:14px; bottom:14px; max-width:min(330px,80vw);
          background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:11px 13px; font-size:13px; line-height:1.55; display:none }
  #info b { display:block; margin-bottom:5px; font-size:14px }
  #info ul { margin:6px 0 0; padding-left:17px } #info li { margin:2px 0 }
  @media (max-width:640px){ .legend{width:100%;margin-left:0} #net{height:60vh} }
</style></head><body>
<header>
  <h1>체류자격 지식 그래프</h1>
  <div class="sub">노드 __N__ · 엣지 __E__ — 굵은 선이 <b>멀티홉의 척추</b>(전환·요건·충족)다.
  노드를 누르면 그 개체가 무엇과 이어져 있는지 보인다.</div>
</header>
<div class="bar">
  <input id="q" placeholder="개체 찾기 (예: F-5, 사회통합, 귀화)">
  <select id="rel"><option value="">모든 관계</option>__RELOPTS__</select>
  <select id="typ"><option value="">모든 종류</option>__TYPOPTS__</select>
  <label style="font-size:13px;color:var(--muted)">
    <input type="checkbox" id="spine" style="min-width:auto"> 척추만</label>
  <div class="legend">__LEGEND__</div>
</div>
<div id="net"></div><div id="info"></div>
<script>
const NODES = __NODES__, EDGES = __EDGES__, SPINE = __SPINE__;
const nodes = new vis.DataSet(NODES), edges = new vis.DataSet(EDGES);
const net = new vis.Network(document.getElementById('net'), {nodes, edges}, {
  nodes:{shape:'dot', size:13, font:{size:14, color:getComputedStyle(document.documentElement).getPropertyValue('--fg').trim()}},
  edges:{arrows:{to:{enabled:true, scaleFactor:.5}}, smooth:{type:'dynamic'},
         font:{size:11, align:'middle', strokeWidth:3,
               strokeColor:getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()}},
  physics:{stabilization:{iterations:220}, barnesHut:{gravitationalConstant:-9000, springLength:135}},
  interaction:{hover:true, tooltipDelay:150}
});
function apply(){
  const rel=document.getElementById('rel').value, typ=document.getElementById('typ').value,
        sp=document.getElementById('spine').checked;
  edges.update(EDGES.map(e=>({id:e.id, hidden:(rel&&e.relation!==rel)||(sp&&!SPINE.includes(e.relation))})));
  nodes.update(NODES.map(n=>({id:n.id, hidden:typ&&n.ntype!==typ})));
}
['rel','typ','spine'].forEach(id=>document.getElementById(id).addEventListener('change',apply));
document.getElementById('q').addEventListener('input',e=>{
  const v=e.target.value.trim(); if(!v) return;
  const hit=NODES.find(n=>n.label.includes(v));
  if(hit){ net.selectNodes([hit.id]); net.focus(hit.id,{scale:1.25, animation:true}); show(hit.id); }
});
function show(id){
  const n=NODES.find(x=>x.id===id); if(!n) return;
  const out=EDGES.filter(e=>e.from===id), inn=EDGES.filter(e=>e.to===id);
  const li=a=>a.slice(0,9).map(e=>`<li>${e.from===id?'':'← '}<i>${e.relation}</i> ${
      e.from===id?(NODES.find(x=>x.id===e.to)||{}).label:(NODES.find(x=>x.id===e.from)||{}).label}</li>`).join('');
  const box=document.getElementById('info');
  box.innerHTML=`<b>${n.label}</b><span style="color:var(--muted)">${n.kr}</span>`+
    (out.length?`<ul>${li(out)}</ul>`:'')+(inn.length?`<ul>${li(inn)}</ul>`:'');
  box.style.display='block';
}
net.on('click', p=>{ if(p.nodes.length) show(p.nodes[0]); else document.getElementById('info').style.display='none'; });
</script></body></html>"""


def graph_html():
    nodes, edges = [], []
    for n in G.nodes:
        t = node_type(n)
        color, kr = COLOR[t]
        lb = G.nodes[n].get("label", "")
        nodes.append({"id": n, "label": f"{n}({lb})" if lb else n, "ntype": t, "kr": kr,
                      "color": color, "size": 11 + min(G.degree(n), 14)})
    for i, (u, v, d) in enumerate(G.edges(data=True)):
        r = d["relation"]
        edges.append({"id": i, "from": u, "to": v, "label": r, "relation": r,
                      "width": 2.5 if r in SPINE else 1,
                      "color": {"color": "#1f4e79" if r in SPINE else "#b6bec9"}})
    rels = sorted({e["relation"] for e in edges})
    html = (HTML
            .replace("__N__", str(G.number_of_nodes()))
            .replace("__E__", str(G.number_of_edges()))
            .replace("__NODES__", json.dumps(nodes, ensure_ascii=False))
            .replace("__EDGES__", json.dumps(edges, ensure_ascii=False))
            .replace("__SPINE__", json.dumps(sorted(SPINE)))
            .replace("__RELOPTS__", "".join(f'<option>{r}</option>' for r in rels))
            .replace("__TYPOPTS__", "".join(
                f'<option value="{t}">{kr}</option>' for t, (_c, kr) in COLOR.items()))
            .replace("__LEGEND__", "".join(
                f'<span><i class="dot" style="background:{c}"></i>{kr}</span>'
                for _t, (c, kr) in COLOR.items())))
    return html


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "schema.md"), "w", encoding="utf-8").write(
        "# 스키마 구조도\n\n"
        "`config.json` 과 `output/graph.graphml` 에서 자동 생성한다 "
        "(`python make_views.py`). 괄호 안은 실제로 뽑힌 엣지 수다.\n\n"
        + schema_mermaid() + "\n")
    open(os.path.join(OUT, "graph.html"), "w", encoding="utf-8").write(graph_html())
    print("→ output/schema.md")
    print("→ output/graph.html  (브라우저로 열면 된다)")
    print("")
    print(schema_mermaid())
