#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""growth-hack-tips 套路卡檢索工具（純 Python，不需安裝套件）。

用法（路徑相對於本 skill 資料夾）：
  python3 scripts/ght.py topics                       列出階段與主題（含別名）
  python3 scripts/ght.py scan --stage Activation,Retention   逐張列出範圍內所有卡的標題、使用時機、適用情境（全掃用）
  python3 scripts/ght.py search "試用轉付費 太低" -n 15   依查詢文字撈卡，可加 --stage Revenue --topic paid-conversion
  python3 scripts/ght.py show T296 T349                印出卡片全文
  python3 scripts/ght.py expand T296 --depth 1         順著「相關套路」展開（前置／後續／搭配／替代）
  python3 scripts/ght.py search ... --json             機器可讀輸出

評分方式：查詢切成英數詞與中文二字詞，對每張卡的標題、使用時機、適用情境、主題與別名、步驟、增補範例
分欄位加權計分（出現即計分，不受文字長度影響），再乘以該詞在全部卡片中的罕見度。
"""
import argparse, json, math, os, re, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

STAGE_ORDER = ["Acquisition", "Activation", "Retention", "Referral", "Revenue"]
FIELD_W = {"title": 3.0, "when": 3.0, "ctx": 2.0, "topic": 2.5, "steps": 1.0, "look": 0.8}
CJK = re.compile(r"[一-鿿]+")
EN = re.compile(r"[a-z0-9][a-z0-9+\-\.]*")


def tokens(s):
    s = s.lower()
    out = set(EN.findall(s))
    for run in CJK.findall(s):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def load():
    recs = []
    for fn in sorted(os.listdir(DATA)):
        if fn.startswith("tactics") and fn.endswith(".jsonl"):
            recs += [json.loads(l) for l in open(os.path.join(DATA, fn), encoding="utf-8") if l.strip()]
    recs.sort(key=lambda r: int(r["metadata"]["tactic_id"][1:]))
    tax = json.load(open(os.path.join(DATA, "taxonomy.json"), encoding="utf-8"))
    return recs, tax


def section(text, name, nxt):
    m = re.search(r"## " + re.escape(name) + r"\n\n(.*?)\n\n## " + re.escape(nxt), text, re.S)
    return m.group(1) if m else ""


def build_index(recs, tax):
    alias = {t["id"]: " ".join([t["label"], t["description"]] + t.get("aliases", [])) for t in tax["topics"]}
    stage_alias = {s["id"]: f"{s['id']} {s['label']}" for s in tax["stages"]}
    idx = []
    df = collections.Counter()
    for r in recs:
        m = r["metadata"]; t = r["text"]
        fields = {
            "title": tokens(m["title"]),
            "when": tokens(m["when_to_use"]),
            "ctx": tokens(m["applicable_contexts"]),
            "topic": tokens(" ".join(alias[x] for x in m["topic_ids"]) + " " + " ".join(stage_alias[s] for s in m["aarrr_stages"])),
            "steps": tokens(section(t, "具體步驟", "如何衡量")),
            "look": tokens(section(t, "增補：具體做法範例", "相關套路")),
        }
        alltok = set().union(*fields.values())
        for tok in alltok:
            df[tok] += 1
        idx.append((r, fields))
    n = len(recs)
    idf = {tok: math.log(1 + n / (1 + c)) for tok, c in df.items()}
    return idx, idf


def resolve_topics(tax, wanted):
    """把使用者給的主題 id、中文名或別名對應到 topic id。"""
    out = set()
    for w in wanted:
        w = w.strip().lower()
        if not w:
            continue
        for t in tax["topics"]:
            names = [t["id"].lower(), t["label"].lower()] + [a.lower() for a in t.get("aliases", [])]
            if w in names:
                out.add(t["id"])
    return out


def cmd_topics(tax):
    print("階段（--stage 用 id）")
    for s in tax["stages"]:
        print(f"  {s['id']:12s} {s['label']}")
    print("\n主題（--topic 用 id、中文名或別名皆可）")
    for t in tax["topics"]:
        print(f"  {t['id']:28s} {t['label']}（{t['tactic_count']} 張）")
        print(f"      別名：{'、'.join(t.get('aliases', [])[:10])}")


def cmd_search(recs, tax, q, stages, topics, n, exclude, as_json):
    idx, idf = build_index(recs, tax)
    qt = tokens(q)
    tops = resolve_topics(tax, topics)
    results = []
    for r, fields in idx:
        m = r["metadata"]
        if stages and not (set(stages) & set(m["aarrr_stages"])):
            continue
        if tops and not (tops & set(m["topic_ids"])):
            continue
        if m["tactic_id"] in exclude:
            continue
        score = 0.0
        for tok in qt:
            w = idf.get(tok, 0.0)
            if w == 0.0:
                continue
            for f, fw in FIELD_W.items():
                if tok in fields[f]:
                    score += w * fw
        if score > 0:
            results.append((score, r))
    results.sort(key=lambda x: -x[0])
    results = results[:n]
    if as_json:
        print(json.dumps([{"id": r["metadata"]["tactic_id"], "score": round(s, 2), "title": r["metadata"]["title"],
                           "stages": r["metadata"]["aarrr_stages"], "topics": r["metadata"]["topic_labels"],
                           "when_to_use": r["metadata"]["when_to_use"], "contexts": r["metadata"]["applicable_contexts"]}
                          for s, r in results], ensure_ascii=False, indent=1))
        return
    if not results:
        print("沒有符合的卡片。試試換個說法、拿掉 --stage／--topic，或用 topics 看主題別名。")
        return
    for s, r in results:
        m = r["metadata"]
        print(f"{m['tactic_id']}  [{s:5.1f}]  {'/'.join(m['aarrr_stages'])}  |  {'、'.join(m['topic_labels'])}")
        print(f"      {m['title']}")
        print(f"      時機：{m['when_to_use']}")
        print(f"      情境：{m['applicable_contexts']}")


def cmd_scan(recs, tax, stages, topics, exclude):
    """逐張列出範圍內所有卡的標題、使用時機、適用情境，供全掃判斷。"""
    tops = resolve_topics(tax, topics)
    rows = []
    for r in recs:
        m = r["metadata"]
        if stages and not (set(stages) & set(m["aarrr_stages"])):
            continue
        if tops and not (tops & set(m["topic_ids"])):
            continue
        if m["tactic_id"] in exclude:
            continue
        rows.append(m)
    scope = "、".join(stages) if stages else "全部階段"
    if tops:
        scope += "；主題 " + "、".join(sorted(tops))
    print(f"範圍：{scope}，共 {len(rows)} 張（已排除 {len(exclude)} 張）\n")
    for m in rows:
        print(f"{m['tactic_id']}｜{m['title']}")
        print(f"  時機：{m['when_to_use']}")
        print(f"  情境：{m['applicable_contexts']}")


def cmd_show(recs, ids):
    byid = {r["metadata"]["tactic_id"]: r for r in recs}
    for i in ids:
        i = i.upper()
        if i not in byid:
            print(f"找不到 {i}\n"); continue
        print(byid[i]["text"]); print("=" * 60)


def cmd_expand(recs, ids, depth, full):
    byid = {r["metadata"]["tactic_id"]: r for r in recs}
    seen = set()
    frontier = [i.upper() for i in ids]
    for d in range(depth):
        nxt = []
        for i in frontier:
            if i not in byid or i in seen:
                continue
            seen.add(i)
            m = byid[i]["metadata"]
            print(f"\n{'#' * (d + 1)} {i}｜{m['title']}")
            for rel in m.get("related", []):
                rm = byid[rel["id"]]["metadata"]
                print(f"  - {rel['relation']}：{rel['id']}｜{rel['title']}")
                print(f"        時機：{rm['when_to_use']}")
                if full:
                    print(f"        情境：{rm['applicable_contexts']}")
                nxt.append(rel["id"])
        frontier = nxt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("topics")
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("-n", type=int, default=12)
    s.add_argument("--stage", default="", help="逗號分隔，例如 Revenue,Retention")
    s.add_argument("--topic", default="", help="逗號分隔，可用 id、中文名或別名")
    s.add_argument("--exclude", default="", help="逗號分隔的卡號")
    s.add_argument("--json", action="store_true")
    sc = sub.add_parser("scan")
    sc.add_argument("--stage", default="", help="逗號分隔，例如 Activation,Retention；省略則掃全部 351 張")
    sc.add_argument("--topic", default="", help="逗號分隔，可用 id、中文名或別名；省略則不限主題")
    sc.add_argument("--exclude", default="", help="逗號分隔的卡號")
    sh = sub.add_parser("show"); sh.add_argument("ids", nargs="+")
    ex = sub.add_parser("expand"); ex.add_argument("ids", nargs="+"); ex.add_argument("--depth", type=int, default=1); ex.add_argument("--full", action="store_true")
    a = ap.parse_args()
    recs, tax = load()
    if a.cmd == "topics":
        cmd_topics(tax)
    elif a.cmd == "search":
        stages = [x.strip() for x in a.stage.split(",") if x.strip()]
        bad = [x for x in stages if x not in STAGE_ORDER]
        if bad:
            sys.exit(f"--stage 只接受 {STAGE_ORDER}，收到 {bad}")
        topics = [x for x in a.topic.split(",") if x.strip()]
        exclude = {x.strip().upper() for x in a.exclude.split(",") if x.strip()}
        cmd_search(recs, tax, a.query, stages, topics, a.n, exclude, a.json)
    elif a.cmd == "scan":
        stages = [x.strip() for x in a.stage.split(",") if x.strip()]
        bad = [x for x in stages if x not in STAGE_ORDER]
        if bad:
            sys.exit(f"--stage 只接受 {STAGE_ORDER}，收到 {bad}")
        topics = [x for x in a.topic.split(",") if x.strip()]
        exclude = {x.strip().upper() for x in a.exclude.split(",") if x.strip()}
        cmd_scan(recs, tax, stages, topics, exclude)
    elif a.cmd == "show":
        cmd_show(recs, a.ids)
    elif a.cmd == "expand":
        cmd_expand(recs, a.ids, a.depth, a.full)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
