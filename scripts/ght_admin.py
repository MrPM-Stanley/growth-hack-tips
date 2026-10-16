#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""growth-hack-tips 卡片維護工具（純 Python，不需安裝套件）。

已安裝的 skill 資料夾是唯讀的：先把整個 skill 資料夾複製到工作資料夾，再對複本跑這支程式。

用法（路徑相對於 skill 資料夾）：
  python3 scripts/ght_admin.py overlap card.md          列出和這張卡最像的既有卡，供判斷重疊
  python3 scripts/ght_admin.py add card.md --dry-run    檢查並預覽要寫入的卡，不改任何檔案
  python3 scripts/ght_admin.py add card.md              以新卡號寫入（卡號自動接最大號）
  python3 scripts/ght_admin.py add card.md --replace T145   用這份 MD 整張覆寫既有卡（保留卡號與位置）
  python3 scripts/ght_admin.py remove T145             刪卡，並清掉其他卡指向它的相關套路
  python3 scripts/ght_admin.py validate                全庫檢查，有錯誤時以非零狀態結束

卡片 MD 必須是本資料集的格式（見 references/update-sop.md 的「卡片格式」）。
add、replace、remove 會一併處理：相關套路雙向連結、content_sha256、taxonomy.json 的 tactic_count、
SKILL.md 描述與 data/README.md 裡的總張數。
"""
import argparse, glob, hashlib, json, math, os, re, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, HERE)
import ght  # noqa: E402  共用斷詞與索引

SECTIONS = ["使用時機", "適用情境", "具體步驟", "如何衡量", "為何歸在這些階段",
            "適用限制", "增補：具體做法範例", "相關套路", "來源"]
REL_ORDER = ["前置", "後續", "搭配", "替代"]
INVERSE = {"前置": "後續", "後續": "前置", "搭配": "搭配", "替代": "替代"}
META_KEYS = ["dataset", "schema_version", "tactic_id", "title", "language", "aarrr_stages",
             "aarrr_stage_labels", "topic_ids", "topic_labels", "when_to_use", "applicable_contexts",
             "source_citations", "source_urls", "content_sha256", "supplement", "added", "related"]
MAX_FILE_BYTES = 245_000
# 需要登入或只有分享對象才看得到的連結（公開說明頁如 slack.com/help 不算）
PRIVATE_HOSTS = ("drive.google.com", "docs.google.com", "dropbox.com/s/", "dropbox.com/scl/",
                 "notion.so", "sharepoint.com", "1drv.ms", "app.slack.com", ".slack.com/archives",
                 "figma.com/file", "figma.com/design", "localhost", "127.0.0.1", "//192.168.", "//10.")
URL_RE = re.compile(r"https?://[^\s\)\]>」」，。；]+")
TID_RE = re.compile(r"^T(\d{3,})$")


# ---------- 讀寫 ----------

def load_files():
    """回傳 [(檔名, [記錄...])]，保留原檔案與順序。"""
    out = []
    for fn in sorted(glob.glob(os.path.join(DATA, "tactics-*.jsonl")),
                     key=lambda p: int(re.search(r"tactics-(\d+)", p).group(1))):
        recs = [json.loads(l) for l in open(fn, encoding="utf-8") if l.strip()]
        out.append((fn, recs))
    return out


def all_recs(files):
    return [r for _, recs in files for r in recs]


def load_tax():
    return json.load(open(os.path.join(DATA, "taxonomy.json"), encoding="utf-8"))


def dump_line(r):
    return json.dumps(r, ensure_ascii=False) + "\n"


def save_files(files):
    for fn, recs in files:
        with open(fn, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(dump_line(r))


def save_tax(tax):
    with open(os.path.join(DATA, "taxonomy.json"), "w", encoding="utf-8") as f:
        json.dump(tax, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tid_num(t):
    return int(t[1:])


# ---------- 解析 ----------

def split_sections(text):
    parts = re.split(r"^## (.+?)\s*$", text, flags=re.M)
    head = parts[0]
    secs = collections.OrderedDict()
    for i in range(1, len(parts), 2):
        secs[parts[i].strip()] = parts[i + 1].strip()
    return head, secs


def bullets(block):
    return [re.sub(r"^\s*[-*]\s+", "", l).strip() for l in block.splitlines() if re.match(r"^\s*[-*]\s+", l)]


def match_names(line, table):
    """把「a、b、c」這種行對應到 id。table: [(名稱, id)]，名稱本身可能含頓號，所以用最長比對而不是先切。"""
    names = sorted(table, key=lambda x: -len(x[0]))
    ids, unknown, s = [], [], line.strip()
    while s:
        s = s.lstrip("、,，;； ")
        if not s:
            break
        for name, i in names:
            if s.lower().startswith(name.lower()):
                if i not in ids:
                    ids.append(i)
                s = s[len(name):]
                break
        else:
            m = re.match(r"[^、,，;；]+", s)
            tok = m.group(0).strip()
            unknown.append(tok)
            s = s[m.end():]
    return ids, unknown


def stage_table(tax):
    t = []
    for st in tax["stages"]:
        t += [(f"{st['id']}（{st['label']}）", st["id"]), (f"{st['id']}({st['label']})", st["id"]),
              (st["id"], st["id"]), (st["label"], st["id"])]
    t += [("營收", "Revenue"), ("收入", "Revenue"), ("激活", "Activation"), ("留存率", "Retention"),
          ("推廣", "Referral"), ("拉新", "Acquisition")]
    return t


def topic_table(tax):
    t = []
    for tp in tax["topics"]:
        t += [(tp["id"], tp["id"]), (tp["label"], tp["id"])] + [(a, tp["id"]) for a in tp.get("aliases", [])]
    return t


def suggest_topics(tax, word, k=3):
    wt = ght.tokens(word)
    scored = []
    for tp in tax["topics"]:
        pool = ght.tokens(" ".join([tp["label"], tp["description"]] + tp.get("aliases", [])))
        s = len(wt & pool)
        if s:
            scored.append((s, tp["id"], tp["label"]))
    scored.sort(key=lambda x: -x[0])
    return [f"{i}（{l}）" for _, i, l in scored[:k]]


def parse_card(path, tax, recs_by_id, topic_override=None, stage_override=None):
    """解析卡片 MD，回傳 (card dict, errors, warnings)。card 只含作者提供的內容，卡號另外決定。"""
    raw = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    head, secs = split_sections(raw)
    errs, warns = [], []
    m = re.search(r"^#\s+(.+?)\s*$", head, re.M)
    title = m.group(1).strip() if m else ""
    title = re.sub(r"^T\d+\s*[｜|]\s*", "", title)
    if not title:
        errs.append("缺少「# 標題」行")
    am = re.search(r"^AARRR\s*[：:]\s*(.+)$", head, re.M)
    tm = re.search(r"^主題\s*[：:]\s*(.+)$", head, re.M)
    if stage_override:
        stages = stage_override
    else:
        stages, unk = match_names(am.group(1), stage_table(tax)) if am else ([], [])
        if not am:
            errs.append("缺少「AARRR：」行")
        if unk:
            errs.append(f"認不得的階段：{unk}，只接受 {[s['id'] for s in tax['stages']]}")
    if topic_override:
        topics = topic_override
    else:
        topics, unk = match_names(tm.group(1), topic_table(tax)) if tm else ([], [])
        if not tm:
            errs.append("缺少「主題：」行")
        for u in unk:
            errs.append(f"主題「{u}」不在 taxonomy 裡；最接近：{suggest_topics(tax, u) or '無'}。"
                        "請和使用者確認後用 --topics 指定，或先在 taxonomy.json 新增主題")
    valid_topics = {t["id"] for t in tax["topics"]}
    for t in topics:
        if t not in valid_topics:
            errs.append(f"--topics 給了不存在的主題 id：{t}")
    if not stages:
        errs.append("至少要有一個階段")
    if not topics:
        errs.append("至少要有一個主題")

    for s in SECTIONS:
        if s not in secs:
            errs.append(f"缺少段落「## {s}」")
    extra = [s for s in secs if s not in SECTIONS]
    if extra:
        errs.append(f"多出非標準段落：{extra}（請併入標準段落，例如證據性質、會議備註不收）")
    order = [s for s in secs if s in SECTIONS]
    if order != [s for s in SECTIONS if s in secs]:
        warns.append("段落順序和標準不同，寫入時會依標準順序重排")

    def one_line(name):
        v = " ".join(x.strip() for x in secs.get(name, "").splitlines() if x.strip())
        if "\n" in secs.get(name, "").strip():
            warns.append(f"「{name}」有多行，已合併成一行")
        return v

    card = {
        "title": title, "stages": stages, "topics": topics,
        "when": one_line("使用時機"), "ctx": one_line("適用情境"),
        "body": {s: secs.get(s, "").strip() for s in SECTIONS if s not in ("相關套路", "來源")},
        "sources": bullets(secs.get("來源", "")),  # 含「註：」行；寫入 source_citations 時排除
        "related": [],
    }
    if secs.get("來源", "").strip() and not card["sources"]:
        card["sources"] = [x.strip() for x in secs["來源"].splitlines() if x.strip()]
        warns.append("來源不是項目清單，已逐行轉成項目")
    if not card["sources"]:
        warns.append("沒有來源；寫入後該卡的來源段會標「來源待補」")

    # 內容規範
    look = card["body"].get("增補：具體做法範例", "")
    for need in ("實際長什麼樣", "最小可行版本"):
        if need not in look:
            errs.append(f"增補段缺「{need}」")
    meas = card["body"].get("如何衡量", "")
    for need in ("主要指標", "防止副作用"):
        if need not in meas:
            errs.append(f"如何衡量缺「{need}」")
    if not re.search(r"^\s*1[\.、]", card["body"].get("具體步驟", ""), re.M):
        errs.append("具體步驟要是編號清單（1. 2. 3.）")
    if "證據性質" in raw:
        errs.append("卡片不收「證據性質」段落或字樣")
    for s in ("為何歸在這些階段",):
        b = bullets(card["body"].get(s, ""))
        named = {re.split(r"[：:]", x)[0].strip() for x in b}
        miss = [st for st in stages if st not in named]
        if miss:
            errs.append(f"「為何歸在這些階段」要每個階段一行「- {miss[0]}：理由」，缺 {miss}")
    for u in URL_RE.findall("\n".join(card["sources"])):
        if any(h in u for h in PRIVATE_HOSTS):
            warns.append(f"來源含非公開網址：{u}。不會放進 source_urls；要不要留在卡片文字裡，請和使用者確認")

    for line in bullets(secs.get("相關套路", "")):
        mm = re.match(r"(前置|後續|搭配|替代)\s*[：:]\s*(T\d+)", line)
        if not mm:
            errs.append(f"相關套路格式不對：「{line}」，要寫成「- 搭配：T145」")
            continue
        rel, rid = mm.group(1), mm.group(2)
        if rid not in recs_by_id:
            errs.append(f"相關套路指向不存在的卡：{rid}")
            continue
        card["related"].append((rel, rid))
    if not card["related"]:
        errs.append("至少要有一條相關套路（前置、後續、搭配、替代）")
    return card, errs, warns


# ---------- 組裝 ----------

def citations(src_bullets):
    """來源段裡「註：」開頭的行是說明，不是引用。"""
    return [x for x in src_bullets if not x.startswith("註：")]


def related_list(pairs, by_id):
    seen, out = set(), []
    for rel, rid in sorted(pairs, key=lambda x: (REL_ORDER.index(x[0]), tid_num(x[1]))):
        if (rel, rid) in seen:
            continue
        seen.add((rel, rid))
        out.append({"id": rid, "relation": rel, "title": by_id[rid]["metadata"]["title"]})
    return out


def related_block(rel):
    return "\n".join(f"- {x['relation']}：{x['id']}｜{x['title']}" for x in rel)


def render_text(tid, card, rel, tax):
    sl = {s["id"]: s["label"] for s in tax["stages"]}
    tl = {t["id"]: t["label"] for t in tax["topics"]}
    parts = [f"# {tid}｜{card['title']}",
             "AARRR：" + "、".join(f"{s}（{sl[s]}）" for s in card["stages"]),
             "主題：" + "、".join(tl[t] for t in card["topics"])]
    for s in SECTIONS:
        if s == "使用時機":
            body = card["when"]
        elif s == "適用情境":
            body = card["ctx"]
        elif s == "相關套路":
            body = related_block(rel)
        elif s == "來源":
            body = "\n".join(f"- {x}" for x in card["sources"]) or "- 來源待補"
        else:
            body = card["body"][s]
        parts.append(f"## {s}\n\n{body}")
    return "\n\n".join(parts) + "\n"


def build_record(tid, card, rel, tax, added, supplement):
    text = render_text(tid, card, rel, tax)
    sl = {s["id"]: s["label"] for s in tax["stages"]}
    tl = {t["id"]: t["label"] for t in tax["topics"]}
    urls = [u for u in URL_RE.findall("\n".join(card["sources"])) if not any(h in u for h in PRIVATE_HOSTS)]
    meta = {
        "dataset": tax.get("dataset", "growth-hack-tips"), "schema_version": tax.get("schema_version", "1.1"),
        "tactic_id": tid, "title": card["title"], "language": tax.get("language", "zh-Hant"),
        "aarrr_stages": card["stages"], "aarrr_stage_labels": [sl[s] for s in card["stages"]],
        "topic_ids": card["topics"], "topic_labels": [tl[t] for t in card["topics"]],
        "when_to_use": card["when"], "applicable_contexts": card["ctx"],
        "source_citations": citations(card["sources"]), "source_urls": list(dict.fromkeys(urls)),
        "content_sha256": sha(text), "supplement": supplement, "added": added, "related": rel,
    }
    return {"id": f"growth-hack-tips:{tid}", "text": text, "metadata": meta}


def set_related(rec, rel):
    """改一張既有卡的相關套路：同步 metadata 與正文段落，重算雜湊。"""
    rec["metadata"]["related"] = rel
    t = rec["text"]
    new = re.sub(r"(## 相關套路\n\n)(.*?)(\n\n## 來源)", lambda m: m.group(1) + related_block(rel) + m.group(3),
                 t, count=1, flags=re.S)
    if new == t and related_block(rel) not in t:
        raise SystemExit(f"{rec['metadata']['tactic_id']} 找不到相關套路段，無法更新")
    rec["text"] = new
    rec["metadata"]["content_sha256"] = sha(new)


def recount(tax, recs):
    c = collections.Counter(t for r in recs for t in r["metadata"]["topic_ids"])
    for tp in tax["topics"]:
        tp["tactic_count"] = c.get(tp["id"], 0)


def sync_doc_counts(old, new):
    """把 SKILL.md、data/README.md 與 ght.py 裡的「舊總數 張／筆」改成新總數，回傳改到的檔案。"""
    if old == new:
        return []
    changed = []
    for fn in (os.path.join(ROOT, "SKILL.md"), os.path.join(DATA, "README.md"), os.path.join(HERE, "ght.py")):
        if not os.path.exists(fn):
            continue
        s = open(fn, encoding="utf-8").read()
        s2 = re.sub(rf"(?<![\d.]){old}(\s*)(張|筆)", rf"{new}\1\2", s)
        if s2 != s:
            open(fn, "w", encoding="utf-8").write(s2)
            changed.append(os.path.relpath(fn, ROOT))
    return changed


def apply_links(files, tid, new_rel, old_rel):
    """依新舊相關套路的差異，替對方卡補上或移除反向連結。"""
    recs = all_recs(files)
    by = {r["metadata"]["tactic_id"]: r for r in recs}
    new_set = {(x["relation"], x["id"]) for x in new_rel}
    old_set = {(x["relation"], x["id"]) for x in old_rel}
    touched = set()
    for rel, rid in new_set - old_set:
        other = by[rid]
        pairs = {(x["relation"], x["id"]) for x in other["metadata"]["related"]}
        pairs.add((INVERSE[rel], tid))
        set_related(other, related_list(pairs, by))
        touched.add(rid)
    for rel, rid in old_set - new_set:
        if rid not in by:
            continue
        other = by[rid]
        pairs = {(x["relation"], x["id"]) for x in other["metadata"]["related"]} - {(INVERSE[rel], tid)}
        set_related(other, related_list(pairs, by))
        touched.add(rid)
    return sorted(touched, key=tid_num)


# ---------- 指令 ----------

def cmd_overlap(path, n):
    """列出可能重疊的既有卡。分數只排順序，不下結論；SOP 另要求逐張讀同階段標題與全文。"""
    files = load_files(); recs = all_recs(files); tax = load_tax()
    by = {r["metadata"]["tactic_id"]: r for r in recs}
    card, errs, _ = parse_card(path, tax, by)
    idx, idf = ght.build_index(recs, tax)
    core_q = ght.tokens(card["title"] + " " + card["when"])
    steps_q = ght.tokens(card["body"].get("具體步驟", ""))
    rows = []
    for r, fields in idx:
        core = fields["title"] | fields["when"]
        # 1) 標題＋使用時機對標題＋使用時機：最能代表「同一個做法」
        s_core = sum(idf.get(t, 0) for t in core_q & core) / max(1e-9, sum(idf.get(t, 0) for t in core_q | core))
        # 2) 步驟對步驟：同一套動作
        s_steps = sum(idf.get(t, 0) for t in steps_q & fields["steps"]) / max(
            1e-9, sum(idf.get(t, 0) for t in steps_q | fields["steps"]))
        same_stage = bool(set(card["stages"]) & set(r["metadata"]["aarrr_stages"]))
        rows.append((s_core * 0.6 + s_steps * 0.4 + (0.02 if same_stage else 0), s_core, s_steps, r))
    rows.sort(key=lambda x: -x[0])
    if errs:
        print("（卡片格式還有問題，比對只供參考：" + "；".join(errs[:3]) + "）\n")
    print(f"新卡：{card['title']}\n時機：{card['when']}\n")
    print("最像的既有卡（標題與時機相似度、步驟相似度）。分數只排順序，重疊程度要讀全文判斷：\n")
    for tot, sc, ss, r in rows[:n]:
        m = r["metadata"]
        print(f"{m['tactic_id']}  [時機 {sc:.2f} | 步驟 {ss:.2f}]  {'/'.join(m['aarrr_stages'])}")
        print(f"      {m['title']}")
        print(f"      時機：{m['when_to_use']}")
    print("\n下一步：照 references/update-sop.md 第 2 步，還要逐張讀同階段卡的標題、補兩次換句話搜尋，")
    print("再用 ght.py show 讀候選卡全文，判斷完全重疊、部分重疊或不重疊。")


def cmd_add(path, replace, topics, stages, added, supplement, dry):
    files = load_files(); recs = all_recs(files); tax = load_tax()
    by = {r["metadata"]["tactic_id"]: r for r in recs}
    old_total = len(recs)
    card, errs, warns = parse_card(path, tax, by, topics, stages)
    if replace:
        replace = replace.upper()
        if replace not in by:
            errs.append(f"--replace 指定的 {replace} 不存在")
        tid = replace
    else:
        tid = f"T{max(tid_num(t) for t in by) + 1:03d}"
    card["related"] = [(rel, rid) for rel, rid in card["related"] if rid != tid]
    for w in warns:
        print("提醒：" + w)
    if errs:
        print("\n無法寫入，請先修正：")
        for e in errs:
            print("  - " + e)
        sys.exit(1)
    rel = related_list(card["related"], by)
    if replace:
        old = by[tid]
        rec = build_record(tid, card, rel, tax, old["metadata"].get("added"),
                           supplement or old["metadata"].get("supplement"))
        old_rel = old["metadata"]["related"]
    else:
        rec = build_record(tid, card, rel, tax, added, supplement)
        old_rel = []
    if dry:
        print(f"\n預覽（{'覆寫' if replace else '新增'} {tid}，尚未寫入）：\n")
        print(rec["text"])
        print(json.dumps({k: v for k, v in rec["metadata"].items() if k != "related"}, ensure_ascii=False, indent=1))
        return
    if replace:
        for fn, rs in files:
            for i, r in enumerate(rs):
                if r["metadata"]["tactic_id"] == tid:
                    rs[i] = rec
        where = "原位置"
    else:
        fn, rs = files[-1]
        if os.path.getsize(fn) + len(dump_line(rec).encode("utf-8")) > MAX_FILE_BYTES:
            k = int(re.search(r"tactics-(\d+)", fn).group(1)) + 1
            fn = os.path.join(DATA, f"tactics-{k}.jsonl")
            files.append((fn, [rec]))
        else:
            rs.append(rec)
        where = os.path.basename(fn)
    touched = apply_links(files, tid, rel, old_rel)
    recs = all_recs(files)
    recount(tax, recs)
    save_files(files); save_tax(tax)
    docs = sync_doc_counts(old_total, len(recs))
    print(f"\n已{'覆寫' if replace else '新增'} {tid}（{where}）：{card['title']}")
    print(f"反向連結更新：{', '.join(touched) if touched else '無'}")
    print(f"總張數：{old_total} → {len(recs)}；已同步張數的文件：{', '.join(docs) if docs else '無'}")
    print("接著跑 validate，並在 data/README.md 的變更紀錄補一筆。")


def cmd_remove(tid):
    tid = tid.upper()
    files = load_files(); tax = load_tax()
    recs = all_recs(files)
    by = {r["metadata"]["tactic_id"]: r for r in recs}
    if tid not in by:
        sys.exit(f"找不到 {tid}")
    old_total = len(recs)
    old_rel = by[tid]["metadata"]["related"]
    touched = apply_links(files, tid, [], old_rel)
    files = [(fn, [r for r in rs if r["metadata"]["tactic_id"] != tid]) for fn, rs in files]
    recs = all_recs(files)
    recount(tax, recs)
    save_files(files); save_tax(tax)
    docs = sync_doc_counts(old_total, len(recs))
    print(f"已刪除 {tid}；移除反向連結：{', '.join(touched) if touched else '無'}")
    print(f"總張數：{old_total} → {len(recs)}；已同步張數的文件：{', '.join(docs) if docs else '無'}")


def cmd_validate():
    files = load_files(); tax = load_tax(); recs = all_recs(files)
    errs, warns = [], []
    by = {}
    for r in recs:
        t = r["metadata"]["tactic_id"]
        if t in by:
            errs.append(f"{t}：卡號重複")
        by[t] = r
    stages = {s["id"]: s["label"] for s in tax["stages"]}
    topics = {t["id"]: t["label"] for t in tax["topics"]}
    for r in recs:
        m, t, tid = r["metadata"], r["text"], r["metadata"]["tactic_id"]
        e = lambda msg: errs.append(f"{tid}：{msg}")
        if list(r.keys()) != ["id", "text", "metadata"]:
            e("記錄欄位順序應為 id、text、metadata")
        if r["id"] != f"growth-hack-tips:{tid}":
            e("id 與 tactic_id 不一致")
        if list(m.keys()) != META_KEYS:
            e(f"metadata 欄位不符：{sorted(set(META_KEYS) ^ set(m.keys()))}")
            continue
        if m["content_sha256"] != sha(t):
            e("content_sha256 未重算")
        if not t.startswith(f"# {tid}｜{m['title']}\n"):
            e("正文標題行與 tactic_id／title 不一致")
        head, secs = split_sections(t)
        if list(secs.keys()) != SECTIONS:
            e(f"段落不符標準：{list(secs.keys())}")
            continue
        if secs["使用時機"] != m["when_to_use"]:
            e("使用時機與 metadata 不一致")
        if secs["適用情境"] != m["applicable_contexts"]:
            e("適用情境與 metadata 不一致")
        for s in m["aarrr_stages"]:
            if s not in stages:
                e(f"不存在的階段 {s}")
        if m["aarrr_stage_labels"] != [stages.get(s) for s in m["aarrr_stages"]]:
            e("aarrr_stage_labels 與階段不對應")
        for x in m["topic_ids"]:
            if x not in topics:
                e(f"不存在的主題 {x}")
        if m["topic_labels"] != [topics.get(x) for x in m["topic_ids"]]:
            e("topic_labels 與主題不對應")
        if "AARRR：" + "、".join(f"{s}（{stages.get(s)}）" for s in m["aarrr_stages"]) not in head:
            e("正文 AARRR 行與 metadata 不一致")
        if "主題：" + "、".join(topics.get(x, x) for x in m["topic_ids"]) not in head:
            e("正文主題行與 metadata 不一致")
        sb = bullets(secs["來源"])
        if m["source_citations"] not in (sb, citations(sb)) and not (
                not m["source_citations"] and "來源待補" in secs["來源"]):
            e("來源段與 source_citations 不一致")
        for u in m["source_urls"]:
            if u not in t:
                e(f"source_urls 有正文沒有的網址 {u}")
            if any(h in u for h in PRIVATE_HOSTS):
                e(f"source_urls 含非公開網址 {u}")
        for u in URL_RE.findall(t):
            if any(h in u for h in PRIVATE_HOSTS):
                warns.append(f"{tid}：正文含非公開網址 {u}，公開發佈前請確認")
        look = secs["增補：具體做法範例"]
        for need in ("實際長什麼樣", "最小可行版本"):
            if need not in look:
                e(f"增補段缺「{need}」")
        if "證據性質" in t:
            e("含「證據性質」")
        if not m["related"]:
            e("沒有任何相關套路")
        if secs["相關套路"] != related_block(m["related"]):
            e("相關套路段落與 metadata.related 不一致")
        if m["related"] != related_list([(x["relation"], x["id"]) for x in m["related"] if x["id"] in by], by):
            warns.append(f"{tid}：related 排序、重複或標題與標準不同")
        for x in m["related"]:
            if x["id"] not in by:
                e(f"相關套路指向不存在的 {x['id']}")
                continue
            o = by[x["id"]]["metadata"]
            if x["title"] != o["title"]:
                e(f"相關套路 {x['id']} 的標題過期")
            if not any(y["id"] == tid and y["relation"] == INVERSE[x["relation"]] for y in o["related"]):
                e(f"與 {x['id']} 的「{x['relation']}」缺反向連結")
    c = collections.Counter(x for r in recs for x in r["metadata"]["topic_ids"])
    for tp in tax["topics"]:
        if tp["tactic_count"] != c.get(tp["id"], 0):
            errs.append(f"taxonomy：{tp['id']} 的 tactic_count 應為 {c.get(tp['id'], 0)}")
    for fn, _ in files:
        if os.path.getsize(fn) > 250_000:
            warns.append(f"{os.path.basename(fn)} 超過 250KB，安裝時可能被擋")
    skill = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read()
    desc = re.search(r"^description:(.*)$", skill, re.M)
    if desc and f"{len(recs)} 張" not in desc.group(1):
        errs.append(f"SKILL.md 描述的張數不是 {len(recs)}")
    print(f"共 {len(recs)} 張、{len(files)} 個資料檔。錯誤 {len(errs)}，提醒 {len(warns)}。")
    for x in errs:
        print("錯誤：" + x)
    for x in warns:
        print("提醒：" + x)
    sys.exit(1 if errs else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("overlap"); o.add_argument("card"); o.add_argument("-n", type=int, default=15)
    a = sub.add_parser("add"); a.add_argument("card")
    a.add_argument("--replace", default="", help="覆寫既有卡號，例如 T145")
    a.add_argument("--topics", default="", help="逗號分隔的主題 id，取代 MD 主題行")
    a.add_argument("--stages", default="", help="逗號分隔的階段 id，取代 MD 的 AARRR 行")
    a.add_argument("--added", default="", help="新卡的 added 年月，預設今天的年月")
    a.add_argument("--supplement", default="", help="增補年月，預設今天的年月（覆寫時預設沿用原值）")
    a.add_argument("--dry-run", action="store_true")
    rm = sub.add_parser("remove"); rm.add_argument("tid")
    sub.add_parser("validate")
    x = ap.parse_args()
    if x.cmd == "overlap":
        cmd_overlap(x.card, x.n)
    elif x.cmd == "add":
        import datetime
        ym = datetime.date.today().strftime("%Y-%m")
        split = lambda s: [y.strip() for y in s.split(",") if y.strip()]
        cmd_add(x.card, x.replace, split(x.topics) or None, split(x.stages) or None,
                x.added or ym, x.supplement or (None if x.replace else ym), x.dry_run)
    elif x.cmd == "remove":
        cmd_remove(x.tid)
    else:
        cmd_validate()


if __name__ == "__main__":
    main()
