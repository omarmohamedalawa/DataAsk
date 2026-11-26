"""Generic planner over a user's uploaded tables. Spec in, parameterized SQL out. No raw SQL from users or models.
Spec: {"measure": {"agg": sum|avg|count|min|max|count_distinct, "table": t, "col": c|None},
       "group_by": [{"table","col","grain"?: month|year}] (max 2), "filters": [{"table","col","op","value"}],
       "order": asc|desc, "top": int|None}
Every table/column must exist in the catalog; joins come only from CONFIRMED relationships, followed many-to-one
(from the fact side to the lookup side) so rows are never duplicated."""
import os, re, json, sqlite3, urllib.request
import schema
from textnorm import norm
AGGS = {"sum", "avg", "count", "min", "max", "count_distinct"}
OPS = {"=": "=", "!=": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<=", "in": "IN", "contains": "LIKE"}
SAFEV = re.compile(r"^[^\x00-\x1f]{0,80}$")

def catalog(st):
    return {t: {c["name"]: c for c in p["columns"]} for t, p in st["tables"].items()}

def _edges(st):
    return [r for r in st["relationships"] if r["status"] == "confirmed" and r["from_table"] in st["tables"] and r["to_table"] in st["tables"]]

def join_path(st, base, targets):
    """BFS from base along from->to edges. Returns ordered list of edges reaching all targets, or raises."""
    E = _edges(st); reach = {base: []}; q = [base]
    while q:
        t = q.pop(0)
        for e in E:
            if e["from_table"] == t and e["to_table"] not in reach:
                reach[e["to_table"]] = reach[t] + [e]; q.append(e["to_table"])
    used, seen = [], set()
    for t in targets:
        if t not in reach:
            raise ValueError(f"no confirmed join from '{base}' to '{t}' (confirm a relationship first)")
        for e in reach[t]:
            if e["id"] not in seen: seen.add(e["id"]); used.append(e)
    return used

def validate(spec, st):
    cat = catalog(st)
    if not isinstance(spec, dict): raise ValueError("bad spec")
    m = spec.get("measure")
    if not isinstance(m, dict) or m.get("agg") not in AGGS: raise ValueError("bad measure")
    if m.get("table") not in cat: raise ValueError("unknown table")
    mc = m.get("col")
    if mc is None:
        if m["agg"] != "count": raise ValueError("measure needs a column")
    else:
        if mc not in cat[m["table"]]: raise ValueError("unknown column")
        if m["agg"] in ("sum", "avg") and cat[m["table"]][mc]["type"] not in ("int", "real"): raise ValueError("sum/avg need a numeric column")
    gb = spec.get("group_by") or []
    if not isinstance(gb, list) or len(gb) > 2: raise ValueError("at most 2 group-by columns")
    g2 = []
    for g in gb:
        if not isinstance(g, dict) or g.get("table") not in cat or g.get("col") not in cat[g["table"]]: raise ValueError("unknown group-by column")
        grain = g.get("grain")
        if grain not in (None, "month", "year"): raise ValueError("bad grain")
        if grain and cat[g["table"]][g["col"]]["type"] != "date": raise ValueError("grain needs a date column")
        g2.append({"table": g["table"], "col": g["col"], "grain": grain})
    fl = spec.get("filters") or []
    if not isinstance(fl, list) or len(fl) > 6: raise ValueError("too many filters")
    f2 = []
    for f in fl:
        if not isinstance(f, dict) or f.get("table") not in cat or f.get("col") not in cat[f["table"]] or f.get("op") not in OPS: raise ValueError("bad filter")
        v = f.get("value"); vals = v if f["op"] == "in" else [v]
        if f["op"] == "in" and (not isinstance(v, list) or not 1 <= len(v) <= 20): raise ValueError("bad filter value")
        for x in vals:
            if isinstance(x, bool) or not isinstance(x, (str, int, float)) or (isinstance(x, str) and not SAFEV.match(x)): raise ValueError("bad filter value")
        f2.append({"table": f["table"], "col": f["col"], "op": f["op"], "value": v})
    order = spec.get("order", "desc")
    if order not in ("asc", "desc"): raise ValueError("bad order")
    top = spec.get("top")
    if top is not None and (not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= 100): raise ValueError("bad top")
    return {"measure": {"agg": m["agg"], "table": m["table"], "col": mc}, "group_by": g2, "filters": f2, "order": order, "top": top}

def build_sql(spec, st):
    base = spec["measure"]["table"]
    tabs = {base} | {x["table"] for x in spec["group_by"]} | {x["table"] for x in spec["filters"]}
    path = join_path(st, base, sorted(tabs - {base}))
    al = {base: "t0"}
    for i, e in enumerate(path, 1): al[e["to_table"]] = f"t{i}"
    q = lambda t, c: f'{al[t]}."{c}"'
    m = spec["measure"]
    if m["agg"] == "count" and not m["col"]: expr = "COUNT(*)"
    elif m["agg"] == "count_distinct": expr = f'COUNT(DISTINCT {q(base, m["col"])})'
    else: expr = f'{m["agg"].upper()}({q(base, m["col"])})'
    gexprs = []
    for g in spec["group_by"]:
        x = q(g["table"], g["col"])
        if g["grain"]: x = f"substr({x},1,{7 if g['grain'] == 'month' else 4})"
        gexprs.append(x)
    sel = ", ".join([f'{x} AS g{i}' for i, x in enumerate(gexprs)] + [f"{expr} AS value"])
    sql = f'SELECT {sel} FROM "{base}" t0'
    for e in path:
        sql += f' LEFT JOIN "{e["to_table"]}" {al[e["to_table"]]} ON {q(e["from_table"], e["from_col"])} = {q(e["to_table"], e["to_col"])}'
    where, params = [], []
    for f in spec["filters"]:
        x = q(f["table"], f["col"])
        if f["op"] == "in": where.append(f'{x} IN ({",".join("?" * len(f["value"]))})'); params += f["value"]
        elif f["op"] == "contains": where.append(f"{x} LIKE ? ESCAPE '\\'"); params.append("%" + str(f["value"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        else: where.append(f'{x} {OPS[f["op"]]} ?'); params.append(f["value"])
    if where: sql += " WHERE " + " AND ".join(where)
    if gexprs:
        sql += " GROUP BY " + ", ".join(gexprs)
        sql += " ORDER BY " + (gexprs[0] + " ASC" if spec["group_by"][0]["grain"] else "value " + spec["order"].upper())
    sql += f' LIMIT {spec["top"] or 100}'
    return sql, params, path

def run(user, spec):
    st = schema.load_state(user); spec = validate(spec, st); sql, params, path = build_sql(spec, st)
    if not re.match(r"^SELECT ", sql) or ";" in sql: raise ValueError("blocked")
    c = sqlite3.connect(f"file:{os.path.abspath(schema.ws_paths(user)[0])}?mode=ro", uri=True)
    try:
        cur = c.execute(sql, params); rows = [list(r) for r in cur.fetchall()]; cols = [d[0] for d in cur.description]
    finally: c.close()
    return {"spec": spec, "sql": sql, "params": params, "joins": [e["id"] for e in path], "columns": cols, "rows": rows}

# ---------- rule planner (Arabic/English) ----------
SYN = {"revenue": ["revenue", "sales", "ايراد", "مبيعات", "المبيعات"], "price": ["price", "سعر"], "qty": ["qty", "quantity", "units", "كميه", "كمية"],
       "profit": ["profit", "ربح"], "amount": ["amount", "value", "قيمه", "مبلغ"]}
def _toks(s): return [t for t in re.split(r"[^\w]+", norm(s)) if t]

def rule_plan(question, st):
    cat = catalog(st); q = " " + norm(question) + " "; qt = set(_toks(question))
    if not cat: raise ValueError("upload a table first")
    def score(name):
        parts = _toks(name.replace("_", " ")); s = sum(1 for p in parts if p in qt or (len(p) > 3 and p in q))
        for k, syns in SYN.items():
            if k in name and any(norm(x) in q for x in syns): s += 1
        return s
    agg = "sum"
    if any(w in q for w in [" average ", " avg ", "متوسط"]): agg = "avg"
    elif any(w in q for w in [" max", "اعلي قيمه", "اكبر"]): agg = "max"
    elif any(w in q for w in [" distinct ", " unique ", "مختلف"]): agg = "count_distinct"
    elif any(w in q for w in [" count ", " number of ", " how many ", "عدد", "كام عميل", "كام طلب"]): agg = "count"
    best = None
    for t, cols in cat.items():
        for n, c in cols.items():
            if c["type"] in ("int", "real") and c["role"] == "measure":
                s = score(n) + (0.5 * score(t)); 
                if best is None or s > best[0]: best = (s, t, n)
    if agg == "count" or best is None or (best[0] == 0 and agg in ("count_distinct",)):
        tbl = max(cat, key=lambda t: (score(t), st["tables"][t]["rows"]))
        measure = {"agg": "count", "table": tbl, "col": None}
    else: measure = {"agg": agg, "table": best[1], "col": best[2]}
    base = measure["table"]
    # group by: phrase after "by"/"per"/"حسب"/"لكل"
    gb = []; blocked = None
    m = re.search(r"(?: by | per | each |حسب |لكل |علي مستوي |على مستوى )(.+)$", q)
    if m:
        seg = set(_toks(m.group(1)))
        for w, gr in (("month", "month"), ("monthly", "month"), ("شهر", "month"), ("year", "year"), ("سنه", "year"), ("yearly", "year")):
            if w in seg:
                for t, cols in cat.items():
                    for n, c in cols.items():
                        if c["type"] == "date":
                            try: join_path(st, base, [t])
                            except ValueError: continue
                            g = {"table": t, "col": n, "grain": gr}
                            if not gb: gb.append(g)
        for t, cols in cat.items():
            for n, c in cols.items():
                if c["role"] in ("dimension", "date") and any(p in seg for p in _toks(n.replace("_", " "))):
                    try: join_path(st, base, [t])
                    except ValueError as e: blocked = blocked or str(e); continue
                    g = {"table": t, "col": n, "grain": "month" if c["type"] == "date" else None}
                    if g not in gb: gb.append(g)
    if blocked and not gb: raise ValueError(blocked)
    gb = gb[:2]
    filters = []
    for t, cols in cat.items():
        for n, c in cols.items():
            for v in c.get("values", []) or []:
                if len(v) >= 2 and (" " + norm(v) + " ") in q:
                    try: join_path(st, base, [t])
                    except ValueError: continue
                    filters.append({"table": t, "col": n, "op": "=", "value": v})
    top = None; mt = re.search(r"(?:top|اعلي|اعلى|اكتر|افضل)\s*(\d+)", q)
    if mt: top = int(mt.group(1))
    return {"measure": measure, "group_by": gb, "filters": filters[:6], "order": "asc" if any(w in q for w in ["lowest", "اقل", "bottom"]) else "desc", "top": top}

# ---------- LLM planner (spec only; validated) ----------
def llm_plan(question, st):
    key = os.environ.get("LLM_API_KEY")
    if not key: return None
    cat = {t: {n: {"type": c["type"], "role": c["role"], **({"values": c["values"][:15]} if c.get("values") else {})} for n, c in cols.items()} for t, cols in catalog(st).items()}
    rel = [r["id"] for r in _edges(st)]
    sysm = ("Convert the question (Arabic or English) into a JSON spec over the given tables. JSON only, no SQL.\n"
            'Spec: {"measure":{"agg":"sum|avg|count|min|max|count_distinct","table":T,"col":C or null},"group_by":[{"table":T,"col":C,"grain":"month|year|null"}],'
            '"filters":[{"table":T,"col":C,"op":"=|!=|>|<|>=|<=|in|contains","value":V}],"order":"asc|desc","top":int or null}\n'
            "Use only these tables/columns and filter values exactly as listed. Measure table should be the fact table; group_by/filters may use tables reachable by the confirmed relationships.\n"
            "Catalog: " + json.dumps(cat, ensure_ascii=False) + "\nConfirmed relationships (from->to): " + json.dumps(rel))
    body = {"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"), "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sysm}, {"role": "user", "content": question[:500]}]}
    req = urllib.request.Request(os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: d = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
        spec = validate(d, st); build_sql(spec, st); return spec
    except Exception: return None

def ask_multi(user, question, use_llm=True):
    st = schema.load_state(user); planner = "llm"
    spec = llm_plan(question, st) if use_llm else None
    if spec is None: planner = "rules"; spec = validate(rule_plan(question, st), st)
    out = run(user, spec); out["planner"] = planner
    out["chart"] = {"type": "line" if spec["group_by"] and spec["group_by"][0]["grain"] else "bar", "labels": [" / ".join(str(x) for x in r[:-1]) for r in out["rows"]],
                    "values": [r[-1] for r in out["rows"]], "title": "value"} if spec["group_by"] and out["rows"] else None
    m = spec["measure"]; out["answer"] = f'{m["agg"]}({m["col"] or "*"}) on {m["table"]}' + (" by " + ", ".join(g["col"] for g in spec["group_by"]) if spec["group_by"] else "") + (" via " + ", ".join(out["joins"]) if out["joins"] else "")
    return out
